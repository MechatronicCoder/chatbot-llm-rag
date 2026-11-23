"""Un test por cada error que se vio en la demo publicada, con el caso real en el nombre.

Errores reportados el 23/11: ofreció un libro agotado, ofreció 12 ejemplares y luego
dijo "máximo 10", negó tener un libro que sí tenía, cobró solo con oír "transferencia",
prometió plazos que no consultó y el panel mostró "? × libro" tras el despliegue.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core.tarjetas import luhn_valido, marca_de, tarjetas_en
from app.persistencia.db import BaseDatos, ErrorPedido
from app.servicios.chat import ServicioChat
from app.servicios.turno import Acumulado
from app.servicios.verificacion import revisar_respuesta
from tests.datos_prueba import ISBN_AGOTADO, ISBN_MAGO, ISBN_SUMAS
from tests.dobles import ModeloFalso, respuesta
from tests.test_api import cliente_con

VISA = "4242 4242 4242 4242"  # número de prueba publicado por las pasarelas de pago
MASTERCARD = "5555555555554444"


# -- 1. ofreció un libro agotado como opción ------------------------------------------
def test_un_agotado_no_se_ofrece_como_opcion_pero_si_se_informa(bd: BaseDatos) -> None:
    # pedido exacto: se muestra para poder decir que está agotado
    assert [lb["isbn"] for lb in bd.buscar_libros("el ladrón del tiempo")] == [ISBN_AGOTADO]
    # como opción entre otras ("ladrón" o "magos"), el agotado no aparece
    opciones = {lb["isbn"] for lb in bd.buscar_libros("ladrón de magos")}
    assert ISBN_MAGO in opciones and ISBN_AGOTADO not in opciones
    assert ISBN_AGOTADO not in {lb["isbn"] for lb in bd.buscar_libros("", limite=20)}


# -- 2. ofreció 12 ejemplares y luego dijo "máximo 10" --------------------------------
def test_se_pueden_agregar_tantos_como_haya_en_stock(bd: BaseDatos) -> None:
    with bd.conectar() as con:
        con.execute("update libros set stock = 15 where isbn = ?", (ISBN_MAGO,))
    carrito = bd.actualizar_carrito("ana", [{"isbn": ISBN_MAGO, "cantidad": 12}], "lima")
    assert carrito["unidades"] == 12
    with pytest.raises(ErrorPedido, match="Solo quedan 15"):
        bd.actualizar_carrito("ana", [{"isbn": ISBN_MAGO, "cantidad": 16}])


# -- 3. negó tener un libro: tomó los medios de pago por títulos -------------------------
def test_los_medios_de_pago_en_negrita_no_son_libros(bd: BaseDatos) -> None:
    texto = (
        "Para comprar *La escuela de magos* elige cómo pagar: **Tarjeta**, **Efectivo** o "
        "**Transferencia bancaria**. El total está en tu carrito y el stock alcanza."
    )
    mensaje = "no, ya quiero comprar, que debo hacer?"
    assert revisar_respuesta(texto, Acumulado(), bd, "ana", mensaje) is None
    # un libro del catálogo presentado con su autor tampoco se marca
    con_autor = "Te recomiendo *La escuela de magos*, de Rosa Varita, con stock disponible."
    assert revisar_respuesta(con_autor, Acumulado(), bd, "ana", mensaje) is None


def test_la_respuesta_con_medios_de_pago_llega_sin_correcciones(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    texto = "Puedes pagar con **Tarjeta**, **Efectivo** o **Transferencia bancaria**."
    modelo.respuestas = [respuesta(texto)]
    r = servicio_con_modelo.responder("no, ya quiero comprar, que debo hacer?", "ana", "s-d3")
    assert r.respuesta == texto and len(modelo.llamadas) == 1


# -- 4. cobró solo con oír "con transferencia bancaria" ----------------------------------
def test_el_cliente_paga_con_el_boton_de_la_tarjeta(tmp_path: Path) -> None:
    cliente = cliente_con(tmp_path)
    bd: BaseDatos = cliente.app.state.estado.bd  # type: ignore[attr-defined]
    pedido = bd.actualizar_carrito("u1", [{"isbn": ISBN_MAGO, "cantidad": 1}], "lima")
    url = f"/api/pedidos/{pedido['pedido_id']}/pago"
    sin_tarjeta = cliente.post(url, json={"usuario_id": "u1", "metodo": "tarjeta"})
    assert sin_tarjeta.status_code == 409 and "tarjetas guardadas" in sin_tarjeta.json()["detail"]
    ajeno = cliente.post(url, json={"usuario_id": "otro", "metodo": "efectivo"})
    assert ajeno.status_code == 409
    pagado = cliente.post(url, json={"usuario_id": "u1", "metodo": "transferencia"}).json()
    assert pagado["pedido"]["estado"] == "pagado" and pagado["monto"] == 79.9
    assert "pagado" in pagado["resumen"] and pagado["tarjeta"] is None
    otra_vez = cliente.post(url, json={"usuario_id": "u1", "metodo": "transferencia"})
    assert otra_vez.status_code == 409


# -- 5. prometió plazos y comprobantes que no consultó -----------------------------------
def test_no_promete_plazos_sin_consultar_las_politicas(bd: BaseDatos) -> None:
    texto = (
        "Tu pedido está listo. Te enviaremos un comprobante y la guía de envío a tu correo "
        "en 24 horas hábiles. Llega en 1 a 3 días hábiles. ¿Algo más?"
    )
    problema = revisar_respuesta(texto, Acumulado(), bd, "ana", "gracias")
    assert problema is not None and "buscar_politica" in problema.instruccion
    assert problema.respaldo == "Tu pedido está listo. ¿Algo más?"
    consultada = Acumulado(usadas=[{"nombre": "buscar_politica", "ok": True}])
    assert revisar_respuesta(texto, consultada, bd, "ana", "gracias") is None


# -- 6. el panel mostró "? × libro": el navegador usaba el JavaScript viejo --------------
def test_el_frontend_se_revalida_en_cada_carga(tmp_path: Path) -> None:
    cliente = cliente_con(tmp_path)
    for ruta in ("/", "/js/panel.js"):
        assert cliente.get(ruta).headers["cache-control"] == "no-cache"
    assert "cache-control" not in cliente.get("/api/health/live").headers


# -- tarjetas guardadas: solo marca y últimos 4 dígitos ----------------------------------
def test_tarjetas_validas_y_su_marca() -> None:
    assert luhn_valido(VISA) and luhn_valido(MASTERCARD)
    assert not luhn_valido("4242 4242 4242 4241") and not luhn_valido("1234")
    assert (marca_de(VISA), marca_de(MASTERCARD), marca_de("378282246310005")) == (
        "Visa", "Mastercard", "American Express",
    )  # fmt: skip
    assert marca_de("6011000990139424") == "Tarjeta"
    assert tarjetas_en(f"mi tarjeta {VISA} y el libro 9780000000033") == ["4242424242424242"]


def test_de_una_tarjeta_solo_se_guardan_marca_y_ultimos_4(bd: BaseDatos) -> None:
    tarjeta = bd.guardar_tarjeta("ana", VISA)
    assert tarjeta["etiqueta"] == "Visa •••• 4242" and tarjeta["ultimos4"] == "4242"
    assert bd.guardar_tarjeta("ana", "4242-4242-4242-4242")["id"] == tarjeta["id"]  # la misma
    with pytest.raises(ErrorPedido, match="no es válido"):
        bd.guardar_tarjeta("ana", "4242 4242 4242 4241")
    with sqlite3.connect(bd.ruta) as con:
        volcado = "\n".join(con.iterdump())
    assert "4242424242424242" not in volcado and "4242 4242" not in volcado
    assert bd.tarjeta_de("otro", tarjeta["id"]) is None  # nadie paga con la tarjeta de otro


def test_pagar_con_una_tarjeta_guardada(bd: BaseDatos) -> None:
    tarjeta = bd.guardar_tarjeta("ana", MASTERCARD)
    pedido = bd.reservar_pedido("ana", ISBN_SUMAS, 1)["pedido_id"]
    otra = bd.guardar_tarjeta("beto", VISA)
    with pytest.raises(ErrorPedido, match="tarjetas guardadas"):
        bd.registrar_pago("ana", pedido, "tarjeta", otra["id"])
    with pytest.raises(ErrorPedido, match="Método no aceptado"):
        bd.registrar_pago("ana", pedido, "cripto")
    pago = bd.registrar_pago("ana", pedido, "tarjeta", tarjeta["id"])
    assert pago["tarjeta"] == "Mastercard •••• 4444" and pago["estado"] == "pagado"


def test_una_tarjeta_escrita_en_el_chat_se_guarda_y_se_tapa(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [respuesta("Guardé tu Visa terminada en 4242; págala con «Pagar».")]
    servicio_con_modelo.responder(f"pagaré con mi tarjeta {VISA}", "ana", "s-tar")
    assert [t["etiqueta"] for t in servicio_con_modelo.bd.tarjetas_de("ana")] == ["Visa •••• 4242"]
    al_modelo = modelo.llamadas[0]["mensajes"][-1]["content"]
    assert "4242 4242" not in al_modelo and "[Tarjeta guardada" in al_modelo
    guardado = servicio_con_modelo.bd.mensajes_de("s-tar")[0]["contenido_redactado"]
    assert "4242 4242" not in guardado and "[TARJETA REDACTADA]" in guardado


def test_la_api_lista_y_guarda_tarjetas_sin_devolver_el_numero(tmp_path: Path) -> None:
    cliente = cliente_con(tmp_path)
    nueva = cliente.post("/api/tarjetas", json={"usuario_id": "u1", "numero": VISA}).json()
    assert nueva["etiqueta"] == "Visa •••• 4242"
    lista = cliente.get("/api/tarjetas/u1").json()
    assert [t["id"] for t in lista["tarjetas"]] == [nueva["id"]]
    mala = cliente.post("/api/tarjetas", json={"usuario_id": "u1", "numero": "4242424242424241"})
    assert mala.status_code == 422 and "4242424242424241" not in mala.text
    bd: BaseDatos = cliente.app.state.estado.bd  # type: ignore[attr-defined]
    pedido = bd.actualizar_carrito("u1", [{"isbn": ISBN_MAGO, "cantidad": 1}], "lima")
    pago = cliente.post(
        f"/api/pedidos/{pedido['pedido_id']}/pago",
        json={"usuario_id": "u1", "metodo": "tarjeta", "tarjeta_id": nueva["id"]},
    ).json()
    assert pago["tarjeta"] == "Visa •••• 4242"


def test_al_querer_pagar_la_respuesta_trae_el_boton(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    """Demo: a "con transferencia bancaria" respondió "pulsa Pagar" sin mostrar la tarjeta."""
    from app.servicios.medios_de_pago import pedido_para_pagar

    bd = servicio_con_modelo.bd
    carrito = bd.actualizar_carrito("ana", [{"isbn": ISBN_MAGO, "cantidad": 1}], "lima")
    modelo.respuestas = [respuesta("Elige transferencia y pulsa «Pagar» en la tarjeta del pedido.")]
    r = servicio_con_modelo.responder("con transferencia bancaria", "ana", "s-pagar")
    assert r.carrito is not None and r.carrito["pedido_id"] == carrito["pedido_id"]
    assert "resumen" in r.carrito
    assert pedido_para_pagar(bd, "ana", "gracias") is None  # no quiere pagar
    reservado = bd.reservar_carrito("ana")
    assert pedido_para_pagar(bd, "ana", "¿cómo pago?")["pedido_id"] == reservado["pedido_id"]  # type: ignore[index]
    assert pedido_para_pagar(bd, "nadie", "quiero pagar") is None


def test_transferencia_bancaria_no_es_un_libro_aunque_la_nombre_el_cliente(bd: BaseDatos) -> None:
    """Demo: a "con transferencia bancaria" el bot dijo "no tenemos el libro Transferencia…"."""
    texto = (
        "Elegiste **Transferencia bancaria**: pulsa «Pagar» en la tarjeta del pedido, con stock."
    )
    assert revisar_respuesta(texto, Acumulado(), bd, "ana", "con transferencia bancaria") is None
    # si el cliente habla de un libro que no tenemos, sí se marca aunque no venga el autor
    alquimista = (
        "Tu experiencia con *El alquimista* no fue buena; su valoración en el catálogo es alta."
    )
    mensaje = "dime porqué el alquimista tiene tan buena valoración"
    problema = revisar_respuesta(alquimista, Acumulado(), bd, "ana", mensaje)
    assert problema is not None and "El alquimista" in problema.motivo


# -- 7. buscar por autor mostraba la tarjeta de su libro agotado -----------------------
def test_buscar_por_autor_no_muestra_su_libro_agotado(bd: BaseDatos) -> None:
    # "libros de Brandon Sanderson" traía *El Héroe de las Eras* (stock 0) entre las tarjetas
    assert ISBN_AGOTADO not in {lb["isbn"] for lb in bd.buscar_libros("clara reloj")}
    assert ISBN_AGOTADO not in {lb["isbn"] for lb in bd.buscar_libros("", autor="Clara Reloj")}
    # pedir justo ese título sí lo trae, para decir que está agotado
    assert [lb["isbn"] for lb in bd.buscar_libros("el ladrón del tiempo")] == [ISBN_AGOTADO]


# -- 8. el panel del catálogo mostraba solo 8 libros ------------------------------------
def test_el_panel_puede_listar_todo_el_catalogo_y_el_modelo_no(
    tmp_path: Path, servicio: ServicioChat
) -> None:
    from app.servicios.herramientas import LIMITE_HERRAMIENTA

    cliente = cliente_con(tmp_path)
    con_stock = cliente.get("/api/libros", params={"limite": 500}).json()
    assert con_stock["total"] == 9  # los 10 libros de prueba menos el agotado
    assert cliente.get("/api/libros", params={"limite": 501}).status_code == 422
    # el modelo ve a lo más 10 libros por búsqueda, aunque pida más
    pedido = {"consulta": "", "limite": 50}
    vistos = servicio.herramientas.ejecutar("buscar_libros", pedido, "a")["libros"]
    assert len(vistos) <= LIMITE_HERRAMIENTA


# -- 9. un clic en la tarjeta ("ver la ficha") terminó con el libro en el carrito ---------
def test_ver_la_ficha_desde_la_tarjeta_no_lo_agrega_al_carrito(bd: BaseDatos) -> None:
    ficha = Acumulado(usadas=[{"nombre": "ficha_libro"}], libros=[{"isbn": ISBN_MAGO, "stock": 8}])
    texto = "Esta es la ficha de *La escuela de magos*. ¿Deseas agregarlo al carrito?"
    pedido_ficha = f"Quiero ver la ficha de «La escuela de magos» (ISBN {ISBN_MAGO})"
    assert revisar_respuesta(texto, ficha, bd, "ana", pedido_ficha) is None
    # pedir el libro sí es comprarlo: preguntar en vez de agregarlo se corrige
    problema = revisar_respuesta(texto, ficha, bd, "ana", "quiero La escuela de magos")
    assert problema is not None and problema.motivo.startswith("pide confirmación")
