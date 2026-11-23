"""Regresiones con los mensajes reales que escribieron los compañeros en la demo publicada."""

from __future__ import annotations

from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.faq import temas
from app.servicios.turno import Acumulado
from app.servicios.verificacion import revisar_respuesta, titulos_ajenos
from tests.dobles import ModeloFalso, llamada, respuesta

HARRY = (
    "¡Esa descripción coincide con **Harry Potter y la piedra filosofal**, de J. K. Rowling!\n"
    "Está disponible en nuestro catálogo:\n- **Precio**: S/ 42.00\n- **Stock**: 12 ejemplares"
)
ADIVINA = "adivina la novela: un niño huérfano que viaja con un mago a un colegio de magia"


# -- la FAQ solo responde si trata del mismo tema --------------------------------
def test_una_pregunta_de_programacion_no_recibe_los_medios_de_pago(
    servicio: ServicioChat,
) -> None:
    for mensaje in (
        "¿Cómo puedo programar una calculadora en Python?",
        "¿cómo puedo hacer un pastel?",
    ):
        decision = servicio.faq.clasificar(mensaje)
        assert decision.ruta == "llm" and decision.similitud >= 0.55  # parecido, pero otro tema
    assert servicio.faq.clasificar("¿cómo puedo pagar?").faq.id == "FAQ-001"  # type: ignore[union-attr]
    assert temas("¿Cómo puedo pagar con tarjetas?") == {"pagar", "tarje"}


# -- películas no son un pedido de libros ------------------------------------------
def test_pedir_peliculas_no_obliga_a_recomendar_libros(bd: BaseDatos) -> None:
    rechazo = "No recomiendo películas: soy el librero virtual. Puedo ayudarte con libros."
    mensaje = "¿Qué películas me recomiendas para este fin de semana?"
    assert revisar_respuesta(rechazo, Acumulado(), bd, "ana", mensaje) is None
    libro_de_pelicula = "¿qué libro me recomiendas parecido a la película Alien?"
    problema = revisar_respuesta("Te recomiendo uno.", Acumulado(), bd, "ana", libro_de_pelicula)
    assert problema is not None and problema.motivo == "recomienda sin consultar el catálogo"


# -- libros que no están en el catálogo ----------------------------------------------
def test_los_titulos_del_catalogo_las_etiquetas_y_los_generos_no_cuentan(bd: BaseDatos) -> None:
    texto = (
        "**La escuela de magos**, de **Rosa Varita**, en **Librería Letra Viva**. "
        "Hay **ciencia ficción**. **Precio**: S/ 69.90. **Envío a Lima:** gratis. "
        "*Libro Inventado*, de Ana Pérez y «Otro Inventado» de Luis Soto."
    )
    assert titulos_ajenos(texto, bd) == ["Libro Inventado", "Otro Inventado"]


def test_ofrecer_un_libro_que_no_tenemos_se_corrige_con_el_catalogo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(HARRY),
        respuesta(tool_calls=[llamada("buscar_libros", consulta="Harry Potter piedra filosofal")]),
        respuesta("No tenemos Harry Potter, pero te recomiendo La escuela de magos."),
    ]
    r = servicio_con_modelo.responder(ADIVINA, "ana", "s-harry")
    correccion = modelo.llamadas[1]["mensajes"][-1]["content"]
    assert "Harry Potter y la piedra filosofal" in correccion and "buscar_libros" in correccion
    assert r.ruta == "herramientas" and "S/ 42.00" not in r.respuesta


def test_si_insiste_el_cliente_sabe_que_no_lo_tenemos(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [respuesta(HARRY), respuesta(HARRY)]
    r = servicio_con_modelo.responder(ADIVINA, "ana", "s-harry-2")
    assert r.respuesta.startswith("No tenemos Harry Potter y la piedra filosofal")
    assert "12 ejemplares" not in r.respuesta


def test_con_el_catalogo_consultado_puede_decir_que_no_esta(bd: BaseDatos) -> None:
    consultado = Acumulado(usadas=[{"nombre": "buscar_libros", "ok": True}])
    texto = "No encontré *Harry Potter y la piedra filosofal* en el catálogo."
    busqueda = '{"consulta": "Harry Potter y la piedra filosofal"}'
    vistos = [{"role": "assistant", "tool_calls": [{"function": {"arguments": busqueda}}]}]
    assert revisar_respuesta(texto, consultado, bd, "ana", ADIVINA, vistos) is None
    # sin haberlo buscado, el título sale de la memoria del modelo: no se nombra
    assert revisar_respuesta(texto, consultado, bd, "ana", ADIVINA) is not None


def test_un_precio_escrito_con_punto_tambien_se_verifica(bd: BaseDatos) -> None:
    from app.servicios.verificacion import montos_sin_respaldo

    assert montos_sin_respaldo("Cuesta S/. 999.99", bd, "ana", "") == ["999.99"]


def test_los_montos_y_los_ids_en_negrita_no_son_titulos(bd: BaseDatos) -> None:
    """En la evaluación, "**S/ 73.30**" se tomó por un título y el bot negó tener Elantris."""
    texto = (
        "Tu carrito **PED-65CC85F3**: **1 × *La escuela de magos* (Rosa Varita)**: S/ 69.90. "
        "**Envío a provincias**: S/ 18.00. **Total: S/ 87.90**. Te faltan **S/ 130.10** para "
        "el envío gratis; tienes **S/ 87.90** en el carrito y el stock alcanza."
    )
    assert titulos_ajenos(texto, bd) == []
    problema = revisar_respuesta(texto, Acumulado(), bd, "ana", "¿cuánto me falta?")
    # el id y los montos inventados se revisan con sus propias reglas, no como "libros"
    assert problema is not None and not problema.motivo.startswith("nombra libros")
