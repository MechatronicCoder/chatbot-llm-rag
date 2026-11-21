"""El turno completo con un modelo simulado (sin red)."""

from __future__ import annotations

import json

import pytest

from app.servicios.chat import ServicioChat
from tests.dobles import ModeloFalso, llamada, respuesta


def eventos_del_turno(servicio: ServicioChat, turno_id: str) -> list[str]:
    return [e["evento"] for e in servicio.registro.leer(limite=50, turno=turno_id)]


def test_turno_con_reserva(servicio_con_modelo: ServicioChat, modelo: ModeloFalso) -> None:
    modelo.respuestas = [
        respuesta(
            tool_calls=[
                llamada("buscar_productos", consulta="laptop"),
                llamada("reservar_pedido", codigo="LAPTOP001", cantidad=2),
            ]
        ),
        respuesta("Reservado."),
    ]
    r = servicio_con_modelo.responder("quiero 2 laptops Dell", "alumno-1", "s-turno")

    assert r.ruta == "accion"
    assert r.modo == "real"
    pedido = servicio_con_modelo.bd.consultar_pedido(r.pedidos[0])
    assert pedido is not None and pedido["cantidad"] == 2
    assert r.tokens == [200, 40]

    mensajes = servicio_con_modelo.bd.mensajes_de("s-turno")
    assert mensajes[-1]["ruta"] == "accion"
    assert mensajes[-1]["tokens_entrada"] == 200

    eventos = eventos_del_turno(servicio_con_modelo, r.turno_id)
    for esperado in (
        "turno.inicio",
        "clasificacion",
        "herramienta.ejecutada",
        "pedido.creado",
        "llm.llamada",
        "turno.fin",
    ):
        assert esperado in eventos


def test_turno_con_rag(servicio_con_modelo: ServicioChat, modelo: ModeloFalso) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("buscar_politica", pregunta="monitor externo")]),
        respuesta("Conecta el cable HDMI y presiona fn+f2 (manual.pdf, pág. 4)."),
    ]
    r = servicio_con_modelo.responder("¿cómo conecto un monitor externo?", "a", "s-rag")
    assert r.ruta == "rag"
    assert r.documentos
    assert "buscar_politica" in r.fuente
    assert "rag.recuperado" in eventos_del_turno(servicio_con_modelo, r.turno_id)


def test_el_resultado_de_la_herramienta_vuelve_al_modelo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("consultar_stock", codigo="LAPTOP001")]),
        respuesta("Quedan unidades."),
    ]
    servicio_con_modelo.responder("¿cuántas laptops Dell quedan?", "a", "s-ida")
    segunda = modelo.llamadas[1]["mensajes"]
    asistente, herramienta = segunda[-2], segunda[-1]
    assert asistente["tool_calls"][0]["function"]["name"] == "consultar_stock"
    assert herramienta["role"] == "tool"
    assert json.loads(herramienta["content"])["codigo"] == "LAPTOP001"


def test_sin_modelo_cae_a_demo(servicio: ServicioChat) -> None:
    r = servicio.responder("¿tienen laptops disponibles?", "alumno", "s-demo")
    assert r.ruta == "demo"
    assert r.modo == "demo"
    assert r.aviso


def test_el_turno_incluye_el_historial(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    servicio_con_modelo.bd.guardar_turno("t-0", "s-hist", "alumno", "hola", "buenas tardes")
    servicio_con_modelo.responder("¿y los envíos a provincia llegan rápido?", "alumno", "s-hist")
    mensajes = modelo.llamadas[-1]["mensajes"]
    assert mensajes[0]["role"] == "system"
    assert {"role": "user", "content": "hola"} in mensajes
    assert mensajes[-1]["content"] == "¿y los envíos a provincia llegan rápido?"


def test_un_saludo_no_recibe_herramientas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    servicio_con_modelo.responder("hola, buenas tardes", "a", "s-saludo")
    assert modelo.llamadas[-1]["herramientas"] is None


def test_las_herramientas_se_ofrecen_al_modelo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    servicio_con_modelo.responder("¿tienen laptops?", "a", "s-tools")
    herramientas = modelo.llamadas[-1]["herramientas"]
    assert len(herramientas) == 7


def test_una_herramienta_repetida_se_reutiliza(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(
            tool_calls=[
                llamada("consultar_stock", codigo="LAPTOP001"),
                llamada("consultar_stock", codigo="LAPTOP001"),
            ]
        ),
        respuesta("listo"),
    ]
    r = servicio_con_modelo.responder("¿cuántas laptops hay?", "a", "s-cache")
    assert len([h for h in r.herramientas if h["nombre"] == "consultar_stock"]) == 1


def test_se_agotan_las_vueltas_y_se_cierra_sin_herramientas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    bucle = [
        respuesta(tool_calls=[llamada("buscar_productos", consulta=f"x{i}")]) for i in range(3)
    ]
    modelo.respuestas = [*bucle, respuesta("cierre")]
    r = servicio_con_modelo.responder("busca muchas cosas", "a", "s-vueltas")
    assert r.respuesta == "cierre"
    assert modelo.llamadas[-1]["herramientas"] is None


def test_mensaje_vacio_o_largo_se_rechaza(servicio: ServicioChat) -> None:
    with pytest.raises(ValueError):
        servicio.responder("   ", "a", "s-x")
    with pytest.raises(ValueError):
        servicio.responder("x" * 3000, "a", "s-x")


def test_la_respuesta_se_valida_antes_de_entregarla(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [respuesta("te vendo pastillas de la competencia")]
    r = servicio_con_modelo.responder("recomiéndame algo", "a", "s-salida")
    assert "pastillas" not in r.respuesta.lower()
    assert "salida.bloqueada" in eventos_del_turno(servicio_con_modelo, r.turno_id)


def test_el_numero_de_turno_crece(servicio: ServicioChat) -> None:
    turnos = [servicio.responder(f"¿tienen laptops? {i}", "a", "s-n").turno for i in range(6)]
    assert turnos == [1, 2, 3, 4, 5, 6]


def test_tema_critico_sin_faq_parecida_se_escala(servicio_con_modelo: ServicioChat) -> None:
    r = servicio_con_modelo.responder("abogado demanda multa", "a", "s-escalar")
    assert r.ruta == "escalar"
    assert r.modo == "sin_llm"


def test_llm_lento_y_salida_con_datos_sensibles(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    lenta = respuesta("Tu tarjeta 4111 1111 1111 1111 quedó registrada.")
    lenta.latencia_s = 9.0
    modelo.respuestas = [lenta]
    r = servicio_con_modelo.responder("¿tienen laptops?", "a", "s-lento")
    assert "4111" not in r.respuesta
    eventos = eventos_del_turno(servicio_con_modelo, r.turno_id)
    assert "llm.lento" in eventos and "salida.redactada" in eventos


def test_el_razonamiento_vuelve_al_modelo_junto_a_las_herramientas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    con_razonamiento = respuesta(tool_calls=[llamada("consultar_stock", codigo="LAPTOP001")])
    con_razonamiento.razonamiento = "hay que mirar el stock"
    modelo.respuestas = [con_razonamiento, respuesta("listo")]
    servicio_con_modelo.responder("¿cuántas Dell quedan?", "a", "s-razon")
    asistente = modelo.llamadas[1]["mensajes"][-2]
    assert asistente["reasoning_content"] == "hay que mirar el stock"


def test_cobro_y_cancelacion_por_herramientas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    bd = servicio_con_modelo.bd
    a_cobrar = bd.reservar_pedido("cli", "LAPTOP001", 1)["pedido_id"]
    a_cancelar = bd.reservar_pedido("cli", "MOUSE001", 1)["pedido_id"]
    modelo.respuestas = [
        respuesta(
            tool_calls=[
                llamada("cobrar_pedido", pedido_id=a_cobrar, metodo="tarjeta"),
                llamada("cancelar_pedido", pedido_id=a_cancelar),
                llamada("consultar_stock", codigo="NO-EXISTE"),
            ]
        ),
        respuesta("Hecho."),
    ]
    r = servicio_con_modelo.responder(f"cobra {a_cobrar} y cancela {a_cancelar}", "cli", "s-acc")
    assert r.ruta == "accion"
    assert len(r.pagos) == 1
    resumenes = [h["resumen"] for h in r.herramientas]
    assert any(s.startswith("pago PAG-") for s in resumenes)
    assert any(s.startswith("error:") for s in resumenes)
    eventos = eventos_del_turno(servicio_con_modelo, r.turno_id)
    assert "pedido.cobrado" in eventos and "pedido.cancelado" in eventos


def test_demo_responde_con_el_catalogo(servicio: ServicioChat) -> None:
    r = servicio.responder("monitor LG UltraGear", "a", "s-demo-cat")
    assert r.ruta == "demo"
    assert r.fuente == "modo demo · catálogo"
    assert "MONITOR" in r.respuesta
