"""El turno completo con un modelo simulado (sin red)."""

from __future__ import annotations

import json

import pytest

from app.servicios.chat import ServicioChat
from tests.datos_prueba import ISBN_CUENTOS, ISBN_DRAGON, ISBN_MAGO, ISBN_SUMAS
from tests.dobles import ModeloFalso, llamada, respuesta


def eventos_del_turno(servicio: ServicioChat, turno_id: str) -> list[str]:
    return [e["evento"] for e in servicio.registro.leer(limite=50, turno=turno_id)]


def test_flujo_de_venta_buscar_y_reservar(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(
            tool_calls=[
                llamada("buscar_libros", consulta="escuela de magos"),
                llamada(
                    "actualizar_carrito", cambios=[{"isbn": ISBN_MAGO, "cantidad": 2}], zona="lima"
                ),
                llamada("reservar_pedido"),
            ]
        ),
        respuesta("Reservado."),
    ]
    r = servicio_con_modelo.responder("quiero 2 de La escuela de magos", "alumno", "s-venta")
    assert r.ruta == "accion" and r.modo == "real"
    pedido = servicio_con_modelo.bd.consultar_pedido(r.pedidos[0])
    assert pedido is not None and pedido["lineas"][0]["cantidad"] == 2
    assert r.carrito is not None and r.carrito["estado"] == "reservado"
    assert [lb["isbn"] for lb in r.libros] == [ISBN_MAGO]
    assert r.tokens == [200, 40]
    assert servicio_con_modelo.bd.mensajes_de("s-venta")[-1]["ruta"] == "accion"
    eventos = eventos_del_turno(servicio_con_modelo, r.turno_id)
    esperados = ("turno.inicio", "clasificacion", "herramienta.ejecutada", "carrito.actualizado",
                 "pedido.creado", "llm.llamada", "turno.fin")  # fmt: skip
    for esperado in esperados:
        assert esperado in eventos


def test_el_historial_guarda_isbn_y_pedido_para_el_turno_siguiente(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(
            tool_calls=[
                llamada("buscar_libros", consulta="escuela de magos"),
                llamada("actualizar_carrito", cambios=[{"isbn": ISBN_MAGO, "cantidad": 1}]),
            ]
        ),
        respuesta("Reservado."),
        respuesta("Listo."),
    ]
    r = servicio_con_modelo.responder("quiero La escuela de magos", "alumno", "s-ref")
    assert "Referencias" not in r.respuesta  # el cliente no ve la nota
    guardado = servicio_con_modelo.bd.mensajes_de("s-ref")[-1]["contenido_redactado"]
    assert f"ISBN {ISBN_MAGO}" in guardado and f"pedido {r.pedidos[0]}" in guardado
    servicio_con_modelo.responder("págalo con tarjeta", "alumno", "s-ref")
    previos = [m["content"] for m in modelo.llamadas[-1]["mensajes"] if m["role"] == "assistant"]
    assert any(f"pedido {r.pedidos[0]}" in m for m in previos)


def test_recomendacion_queda_como_ruta_y_evento(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("recomendar_libros", necesidad="aprender a sumar", edad=5)]),
        respuesta("Te recomiendo Sumas divertidas."),
    ]
    r = servicio_con_modelo.responder("algo para que mi hijo de 5 aprenda a sumar", "a", "s-reco")
    assert r.ruta == "recomendacion"
    assert r.libros[0]["isbn"] == ISBN_SUMAS
    assert "recomendacion.generada" in eventos_del_turno(servicio_con_modelo, r.turno_id)


def test_la_ficha_suma_el_libro_a_las_tarjetas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("ficha_libro", isbn=ISBN_DRAGON)]),
        respuesta("Es una aventura épica."),
    ]
    r = servicio_con_modelo.responder("cuéntame más de La montaña del dragón", "a", "s-ficha")
    assert r.ruta == "herramientas"
    assert r.libros[0]["isbn"] == ISBN_DRAGON and r.libros[0]["sinopsis"]
    assert r.herramientas[0]["resumen"] == f"ficha {ISBN_DRAGON}"


def test_turno_con_rag(servicio_con_modelo: ServicioChat, modelo: ModeloFalso) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("buscar_politica", pregunta="páginas en blanco")]),
        respuesta("Se cambia sin costo (politicas.md, sección 3)."),
    ]
    r = servicio_con_modelo.responder("explícame qué hacen si viene mal impreso", "a", "s-rag")
    assert r.ruta == "rag" and r.documentos
    assert "rag.recuperado" in eventos_del_turno(servicio_con_modelo, r.turno_id)


def test_el_resultado_de_la_herramienta_vuelve_al_modelo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("ficha_libro", isbn=ISBN_SUMAS)]),
        respuesta("Quedan 10."),
    ]
    servicio_con_modelo.responder("¿cuántos quedan de Sumas divertidas?", "a", "s-ida")
    asistente, herramienta = modelo.llamadas[1]["mensajes"][-2:]
    assert asistente["tool_calls"][0]["function"]["name"] == "ficha_libro"
    assert json.loads(herramienta["content"])["stock"] == 10


def test_un_isbn_en_el_mensaje_va_al_modelo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    """Con un ISBN no responde una FAQ parecida: lo resuelven las herramientas."""
    r = servicio_con_modelo.responder(f"quiero el libro {ISBN_CUENTOS}", "a", "s-isbn")
    assert r.ruta != "faq" and modelo.llamadas
    assert r.clasificacion is not None and "accion" in r.clasificacion["motivo"]


def test_sin_modelo_el_demo_busca_primero_en_el_catalogo(servicio: ServicioChat) -> None:
    r = servicio.responder("busco La escuela de magos", "a", "s-demo")
    assert r.ruta == "demo" and r.fuente == "modo demo · catálogo"
    assert "La escuela de magos" in r.respuesta and "S/" in r.respuesta
    assert r.libros[0]["isbn"] == ISBN_MAGO
    assert r.aviso


def test_sin_modelo_el_demo_recomienda_por_contenido_y_edad(servicio: ServicioChat) -> None:
    r = servicio.responder("un libro para que mi hijo de 5 años aprenda a sumar", "a", "s-d0")
    assert r.libros and r.libros[0]["isbn"] == ISBN_SUMAS
    assert all(lb["edad"] in ("4–6 años", "3–6 años") for lb in r.libros)
    por_isbn = servicio.responder(f"¿tienen el {ISBN_CUENTOS}?", "a", "s-d3")
    assert [lb["isbn"] for lb in por_isbn.libros] == [ISBN_CUENTOS]


def test_sin_modelo_el_demo_usa_la_faq_cercana_o_un_aviso(servicio: ServicioChat) -> None:
    cercana = servicio.responder("¿el envío por Olva a Ica cuánto tarda en llegar?", "a", "s-d1")
    assert cercana.fuente.startswith("modo demo · FAQ cercana")
    nada = servicio.responder("cuéntame un chiste de piratas espaciales", "a", "s-d2")
    assert nada.fuente == "modo demo" and "Modo demo" in nada.respuesta


def test_el_turno_incluye_el_historial_y_el_prompt_de_la_tienda(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    servicio_con_modelo.bd.guardar_turno("t-0", "s-hist", "alumno", "hola", "buenas tardes")
    servicio_con_modelo.responder("¿qué novelas de fantasía tienen?", "alumno", "s-hist")
    mensajes = modelo.llamadas[-1]["mensajes"]
    assert mensajes[0]["role"] == "system"
    assert servicio_con_modelo.tienda.nombre in mensajes[0]["content"]
    assert "{tienda}" not in mensajes[0]["content"]
    assert {"role": "user", "content": "hola"} in mensajes


def test_un_saludo_no_recibe_herramientas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    servicio_con_modelo.responder("hola, buenas tardes", "a", "s-saludo")
    assert modelo.llamadas[-1]["herramientas"] is None
    servicio_con_modelo.responder("¿tienen libros de fantasía?", "a", "s-tools")
    assert len(modelo.llamadas[-1]["herramientas"]) == 9


def test_una_herramienta_repetida_se_reutiliza(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("ficha_libro", isbn=ISBN_MAGO)] * 2),
        respuesta("listo"),
    ]
    r = servicio_con_modelo.responder("¿cuántos hay de La escuela de magos?", "a", "s-cache")
    assert len(r.herramientas) == 1


def test_se_agotan_las_vueltas_y_se_cierra_sin_herramientas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    bucle = [respuesta(tool_calls=[llamada("buscar_libros", consulta=f"x{i}")]) for i in range(3)]
    modelo.respuestas = [*bucle, respuesta("cierre")]
    r = servicio_con_modelo.responder("busca muchas cosas", "a", "s-vueltas")
    assert r.respuesta == "cierre" and modelo.llamadas[-1]["herramientas"] is None


def test_mensaje_vacio_o_largo_se_rechaza(servicio: ServicioChat) -> None:
    with pytest.raises(ValueError):
        servicio.responder("   ", "a", "s-x")
    with pytest.raises(ValueError):
        servicio.responder("x" * 3000, "a", "s-x")


def test_la_salida_no_recomienda_sitios_piratas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    # en minúsculas no es un nombre propio: lo frena el filtro de salida, la última barrera
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("recomendar_libros", necesidad="algo para leer")]),
        respuesta("Búscalo gratis en libgen."),
    ]
    r = servicio_con_modelo.responder("¿qué me recomiendas leer?", "a", "s-salida")
    assert "libgen" not in r.respuesta.lower()
    assert "salida.bloqueada" in eventos_del_turno(servicio_con_modelo, r.turno_id)


def test_un_sitio_pirata_con_nombre_propio_ya_lo_frena_la_verificacion(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("recomendar_libros", necesidad="algo para leer")]),
        respuesta("Búscalo gratis en Z-Library."),
        respuesta("Búscalo gratis en Z-Library."),
    ]
    r = servicio_con_modelo.responder("¿qué me recomiendas leer?", "a", "s-salida-2")
    assert "library" not in r.respuesta.lower()
    assert "salida.no_verificada" in eventos_del_turno(servicio_con_modelo, r.turno_id)


def test_llm_lento_y_salida_con_tarjeta(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    lenta = respuesta(f"Tu tarjeta 4111 1111 1111 1111 quedó registrada para {ISBN_MAGO}.")
    lenta.latencia_s = 9.0
    modelo.respuestas = [lenta]
    r = servicio_con_modelo.responder("¿tienen novelas?", "a", "s-lento")
    assert "4111" not in r.respuesta and ISBN_MAGO in r.respuesta  # el ISBN no se toca
    eventos = eventos_del_turno(servicio_con_modelo, r.turno_id)
    assert "llm.lento" in eventos and "salida.redactada" in eventos


def test_el_razonamiento_vuelve_junto_a_las_herramientas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    con_razonamiento = respuesta(tool_calls=[llamada("ficha_libro", isbn=ISBN_MAGO)])
    con_razonamiento.razonamiento = "hay que mirar el stock"
    modelo.respuestas = [con_razonamiento, respuesta("listo")]
    servicio_con_modelo.responder("¿hay stock del de magos?", "a", "s-razon")
    assert modelo.llamadas[1]["mensajes"][-2]["reasoning_content"] == "hay que mirar el stock"


def test_el_modelo_cancela_pero_no_cobra(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    bd = servicio_con_modelo.bd
    a_cobrar = bd.reservar_pedido("cli", ISBN_MAGO, 1)["pedido_id"]
    a_cancelar = bd.reservar_pedido("cli", ISBN_SUMAS, 1)["pedido_id"]
    modelo.respuestas = [
        respuesta(
            tool_calls=[
                llamada("cobrar_pedido", pedido_id=a_cobrar, metodo="tarjeta"),
                llamada("cancelar_pedido", pedido_id=a_cancelar),
                llamada("ficha_libro", isbn="9780000000999"),
            ]
        ),
        respuesta("Cancelé uno; el otro lo pagas con el botón «Pagar»."),
    ]
    r = servicio_con_modelo.responder(f"cobra {a_cobrar} y cancela {a_cancelar}", "cli", "s-acc")
    assert r.ruta == "accion" and not r.pagos
    assert bd.consultar_pedido(a_cobrar)["estado"] == "reservado"  # type: ignore[index]
    resumenes = [h["resumen"] for h in r.herramientas]
    assert any("desconocida" in s for s in resumenes)
    assert "pedido.cancelado" in eventos_del_turno(servicio_con_modelo, r.turno_id)


def test_tema_critico_sin_faq_parecida_se_escala(servicio_con_modelo: ServicioChat) -> None:
    r = servicio_con_modelo.responder("abogado denuncia indecopi", "a", "s-escalar")
    assert r.ruta == "escalar" and r.modo == "sin_llm"


def test_el_numero_de_turno_crece(servicio: ServicioChat) -> None:
    turnos = [servicio.responder(f"busco magos {i}", "a", "s-n").turno for i in range(6)]
    assert turnos == [1, 2, 3, 4, 5, 6]


def test_resumen_de_un_resultado_sin_forma_conocida() -> None:
    from app.servicios.turno import resumir

    assert resumir({"campo_nuevo": 1, "otro": 2}) == "campo_nuevo, otro"
