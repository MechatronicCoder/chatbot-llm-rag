"""Carrito y cotización: subtotal, envío y total los calcula el sistema, no el modelo."""

from __future__ import annotations

import pytest

from app.catalogo.carga import Tienda
from app.catalogo.cotizacion import (
    Linea,
    a_centimos,
    a_soles,
    cotizar,
    detalle,
    montos_en_centimos,
    resumen,
)
from app.persistencia.db import BaseDatos, ErrorPedido
from app.servicios.chat import ServicioChat
from app.servicios.turno import Acumulado
from app.servicios.verificacion import montos_sin_respaldo, revisar_respuesta
from tests.datos_prueba import ISBN_AGOTADO, ISBN_DRAGON, ISBN_GOBLIN, ISBN_MAGO, ISBN_SUMAS
from tests.dobles import ModeloFalso, llamada, respuesta


def linea(precio_cent: int, cantidad: int = 1, isbn: str = "a") -> Linea:
    return Linea(isbn, f"Libro {isbn}", "Autora", cantidad, precio_cent)


def pedido_de(lineas: list[Linea], zona: str | None, tienda: Tienda) -> dict[str, object]:
    return {
        "pedido_id": "PED-1",
        "estado": "carrito",
        **detalle(cotizar(lineas, zona, tienda), tienda),
    }


# -- cotización (funciones puras) -------------------------------------------------
@pytest.mark.parametrize(
    ("subtotal", "zona", "envio"),
    [
        (11999, "lima", 1000),
        (12000, "lima", 0),
        (19999, "provincias", 1800),
        (20000, "provincias", 0),
    ],
)
def test_el_envio_es_gratis_desde_el_minimo_de_cada_zona(
    bd: BaseDatos, subtotal: int, zona: str, envio: int
) -> None:
    c = cotizar([linea(subtotal)], zona, bd.tienda)
    assert c.envio_cent == envio and c.total_cent == subtotal + envio
    assert c.falta_gratis_cent == (1 if envio else None)


def test_sin_zona_sin_libros_o_con_zona_desconocida(bd: BaseDatos) -> None:
    sin_zona = cotizar([linea(5000)], None, bd.tienda)
    assert sin_zona.envio_cent is None and sin_zona.total_cent == 5000
    vacio = cotizar([], "lima", bd.tienda)
    assert vacio.envio_cent is None and vacio.total_cent == 0
    assert cotizar([linea(5000)], "marte", bd.tienda).zona is None


def test_los_montos_se_redondean_en_centimos() -> None:
    assert a_centimos(59.905) == 5991 and a_centimos("0.1") == 10
    assert a_soles(5991) == 59.91
    assert linea(10, cantidad=3).importe_cent == 30  # 3 × 0.10 = 0.30 exacto


def test_una_zona_sin_minimo_cobra_siempre_el_envio() -> None:
    tienda = Tienda.model_validate(
        {
            "nombre": "X",
            "moneda": {"simbolo": "S/", "codigo": "PEN"},
            "envio": {
                "recojo": {"nombre": "recojo en tienda", "costo": 0},
                "lejos": {"nombre": "lejos", "costo": 25},
            },
        }
    )
    c = cotizar([linea(90000)], "lejos", tienda)
    assert c.envio_cent == 2500 and c.falta_gratis_cent is None
    texto = resumen(pedido_de([linea(1000)], None, tienda), tienda)
    assert "recojo en tienda: S/ 0.00; lejos: S/ 25.00" in texto


def test_el_resumen_trae_las_cuentas_hechas(bd: BaseDatos) -> None:
    t = bd.tienda
    dos = [linea(6990, 2, "a"), linea(2990, 1, "b")]
    texto = resumen(pedido_de(dos, "lima", t), t)
    assert "- 2 × «Libro a» de Autora: S/ 69.90 c/u = S/ 139.80" in texto
    assert "- 1 × «Libro b» de Autora: S/ 29.90" in texto
    assert "Envío a Lima Metropolitana: gratis" in texto and "Total: S/ 169.70" in texto
    con_falta = resumen(pedido_de([linea(6990)], "lima", t), t)
    assert "S/ 10.00 (faltan S/ 50.10 para el envío gratis)" in con_falta
    sin_zona = resumen(pedido_de([linea(6990)], None, t), t)
    assert "falta elegir la zona" in sin_zona and "Total: S/ 69.90 sin envío" in sin_zona
    assert "El carrito está vacío." in resumen(pedido_de([], "lima", t), t)
    assert {13980, 6990, 2990, 16970} <= montos_en_centimos(pedido_de(dos, "lima", t))


# -- repositorio -------------------------------------------------------------------
def test_el_carrito_cambia_cantidades_y_quita_libros(bd: BaseDatos) -> None:
    c = bd.actualizar_carrito(
        "ana", [{"isbn": ISBN_MAGO, "cantidad": 1}, {"isbn": ISBN_SUMAS, "cantidad": 2}], "Lima"
    )
    assert (c["subtotal"], c["envio"], c["total"]) == (129.7, 0.0, 129.7)
    c = bd.actualizar_carrito("ana", [{"isbn": ISBN_SUMAS, "cantidad": 1}])
    assert (c["subtotal"], c["envio"], c["falta_para_envio_gratis"]) == (99.8, 10.0, 20.2)
    assert [ln["isbn"] for ln in c["lineas"]] == [ISBN_MAGO, ISBN_SUMAS]  # orden de llegada
    mismo = bd.actualizar_carrito("ana", [{"isbn": ISBN_MAGO, "cantidad": 0}])
    assert mismo["pedido_id"] == c["pedido_id"] and mismo["unidades"] == 1
    assert bd.carrito_de("ana") == mismo and bd.carrito_de("nadie") is None
    bd.actualizar_carrito("ana", [{"isbn": ISBN_SUMAS, "cantidad": 0}])
    assert bd.pedidos_de("ana") == []  # un carrito vacío no se lista


def test_el_carrito_no_aparta_stock_pero_respeta_el_disponible(bd: BaseDatos) -> None:
    bd.actualizar_carrito("ana", [{"isbn": ISBN_GOBLIN, "cantidad": 3}])
    assert bd.obtener_libro(ISBN_GOBLIN)["stock"] == 3  # type: ignore[index]
    with pytest.raises(ErrorPedido, match="Solo quedan 3"):
        bd.actualizar_carrito("ana", [{"isbn": ISBN_GOBLIN, "cantidad": 4}])


def test_el_carrito_tiene_un_limite_de_libros_distintos(
    bd: BaseDatos, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.persistencia.carrito.MAX_LINEAS", 1)
    bd.actualizar_carrito("ana", [{"isbn": ISBN_MAGO, "cantidad": 1}])
    with pytest.raises(ErrorPedido, match="hasta 1 libros"):
        bd.actualizar_carrito("ana", [{"isbn": ISBN_SUMAS, "cantidad": 1}])
    assert bd.carrito_de("ana")["unidades"] == 1  # type: ignore[index]  # nada a medias


def test_un_cambio_de_precio_frena_el_cobro_y_se_avisa_al_reservar(bd: BaseDatos) -> None:
    pedido_id = bd.actualizar_carrito("ana", [{"isbn": ISBN_MAGO, "cantidad": 1}], "lima")[
        "pedido_id"
    ]
    with bd.conectar() as con:
        con.execute("update libros set precio = 75.0 where isbn = ?", (ISBN_MAGO,))
    with pytest.raises(ErrorPedido, match="cambió el precio"):
        bd.registrar_pago("ana", pedido_id, "efectivo")
    assert bd.carrito_de("ana")["total"] == 79.9  # type: ignore[index]  # no se tocó nada
    assert bd.obtener_libro(ISBN_MAGO)["stock"] == 8  # type: ignore[index]
    reservado = bd.reservar_carrito("ana")
    assert "S/ 69.90 a S/ 75.00" in reservado["cambios_de_precio"][0]
    assert reservado["total"] == 85.0


def test_un_libro_retirado_no_se_reserva(bd: BaseDatos) -> None:
    bd.actualizar_carrito("ana", [{"isbn": ISBN_DRAGON, "cantidad": 1}], "lima")
    with bd.conectar() as con:
        con.execute("update libros set activo = 0 where isbn = ?", (ISBN_DRAGON,))
    with pytest.raises(ErrorPedido, match="Solo quedan 0"):
        bd.reservar_carrito("ana")


def test_el_atajo_de_un_libro_valida_cantidad_y_zona(bd: BaseDatos) -> None:
    with pytest.raises(ErrorPedido, match="mayor que cero"):
        bd.reservar_pedido("ana", ISBN_SUMAS, 0)
    with pytest.raises(ErrorPedido, match="Zona de envío desconocida"):
        bd.reservar_pedido("ana", ISBN_SUMAS, 1, zona="marte")
    assert bd.reservar_pedido("ana", ISBN_SUMAS, 1, zona="provincias")["envio"] == 18.0


def test_cobrar_un_carrito_vende_una_fila_por_libro(bd: BaseDatos) -> None:
    pedido = bd.actualizar_carrito(
        "ana", [{"isbn": ISBN_MAGO, "cantidad": 1}, {"isbn": ISBN_SUMAS, "cantidad": 1}], "lima"
    )
    pago = bd.registrar_pago("ana", pedido["pedido_id"], "efectivo")
    assert pago["monto"] == 109.8 and bd.pagos_de(pedido["pedido_id"])[0]["monto"] == 109.8
    ventas = bd.ventas("ana")
    assert [v["isbn"] for v in ventas] == [ISBN_MAGO, ISBN_SUMAS]
    assert all(v["envio"] == 10.0 and v["total"] == 109.8 for v in ventas)
    with pytest.raises(ErrorPedido, match="no se puede cancelar"):
        bd.cancelar_pedido(pedido["pedido_id"])


# -- verificación de montos ------------------------------------------------------------
def test_solo_pasan_montos_con_respaldo(bd: BaseDatos) -> None:
    def sueltos(texto: str, mensaje: str = "") -> list[str]:
        return montos_sin_respaldo(texto, bd, "ana", mensaje)

    assert sueltos("Sin montos.") == []
    assert sueltos("Cuesta S/ 69.90 y el envío S/ 10.") == []  # precio y tarifa
    assert sueltos("Hay uno de S/ 29,90.") == []  # coma decimal
    assert sueltos("Por menos de S/ 45 tengo uno.", "algo por menos de 45 soles") == []
    assert sueltos("Total: S/ 1,234.50 o S/ 99.99") == ["1,234.50", "99.99"]
    bd.actualizar_carrito("ana", [{"isbn": ISBN_MAGO, "cantidad": 2}], "lima")
    assert sueltos("Total: S/ 139.80") == []  # monto de un pedido del cliente
    sin_carrito = revisar_respuesta("Te sale S/ 999 en total.", Acumulado(), bd, "ana", "x")
    assert sin_carrito is not None and "lo calcula el sistema" in sin_carrito.respaldo


# -- el turno ------------------------------------------------------------------------
def test_un_total_inventado_termina_en_el_resumen_del_sistema(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    carro = llamada("actualizar_carrito", cambios=[{"isbn": ISBN_MAGO, "cantidad": 1}], zona="lima")
    modelo.respuestas = [
        respuesta(tool_calls=[carro]),
        respuesta("Listo, el total es S/ 75.00."),
        respuesta("Perdón, son S/ 75.00."),
    ]
    r = servicio_con_modelo.responder("quiero comprar La escuela de magos", "ana", "s-m")
    correccion = modelo.llamadas[2]["mensajes"][-1]["content"]
    assert "75.00" in correccion and "actualizar_carrito" in correccion
    assert "Total: S/ 79.90" in r.respuesta and "75.00" not in r.respuesta


def test_no_se_reserva_un_carrito_vacio_ni_lo_que_se_vendio_mientras_tanto(
    bd: BaseDatos, monkeypatch: pytest.MonkeyPatch
) -> None:
    bd.actualizar_carrito("ana", [{"isbn": ISBN_SUMAS, "cantidad": 1}], "lima")
    bd.actualizar_carrito("ana", [{"isbn": ISBN_SUMAS, "cantidad": 0}])
    with pytest.raises(ErrorPedido, match="carrito está vacío"):
        bd.reservar_carrito("ana")
    # otra venta se llevó el último ejemplar entre la validación y la reserva
    visto = {"isbn": ISBN_AGOTADO, "titulo": "El ladrón del tiempo", "stock": 1}
    monkeypatch.setattr(bd, "obtener_libro", lambda _: visto)
    with pytest.raises(ErrorPedido, match="Solo quedan 0"):
        bd.reservar_pedido("ana", ISBN_AGOTADO, 1)
    assert bd.pedidos_de("ana") == []  # el atajo no dejó nada a medias


def test_pedir_un_libro_concreto_lo_agrega_sin_preguntar(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    buscar = llamada("buscar_libros", consulta="escuela de magos")
    carro = llamada("actualizar_carrito", cambios=[{"isbn": ISBN_MAGO, "cantidad": 1}])
    modelo.respuestas = [
        respuesta(tool_calls=[buscar]),
        respuesta("La tengo a S/ 69.90. ¿Deseas agregarla al carrito?"),
        respuesta(tool_calls=[carro]),
        respuesta("Agregada."),
    ]
    r = servicio_con_modelo.responder("quiero el libro La escuela de magos", "ana", "s-q")
    assert "actualizar_carrito" in modelo.llamadas[2]["mensajes"][-1]["content"]
    assert r.carrito is not None and r.carrito["unidades"] == 1
    buscar_otro = revisar_respuesta(
        "¿De qué tema?",
        Acumulado(usadas=[{"nombre": "buscar_libros"}], libros=[{"isbn": ISBN_MAGO}]),
        servicio_con_modelo.bd,
        "ana",
        "quiero un libro de magia",
    )
    assert buscar_otro is None  # buscar un libro no es pedir uno concreto


def test_las_preguntas_del_carrito_propio_no_las_responde_la_faq() -> None:
    from app.servicios.turno import RE_ACCION

    for mensaje in ("¿cuánto me falta para el envío gratis?", "muéstrame mi carrito",
                    "quita El Silmarillion", "agrégalo"):  # fmt: skip
        assert RE_ACCION.search(mensaje), mensaje
    assert not RE_ACCION.search("¿cuánto cuesta el envío?")


def test_tras_recomendar_o_con_varios_libros_puede_preguntar(bd: BaseDatos) -> None:
    mensaje = "quiero ciencia ficción con robots"
    pregunta = "¿Te interesa alguno?"
    recomendo = Acumulado(
        usadas=[{"nombre": "recomendar_libros", "ok": True}], libros=[{"isbn": ISBN_MAGO}]
    )
    assert revisar_respuesta(pregunta, recomendo, bd, "ana", mensaje) is None
    varios = Acumulado(libros=[{"isbn": ISBN_MAGO}, {"isbn": ISBN_SUMAS}])
    assert revisar_respuesta(pregunta, varios, bd, "ana", "quiero el de magos") is None
