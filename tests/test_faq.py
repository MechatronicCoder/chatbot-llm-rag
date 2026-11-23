"""Clasificador de preguntas frecuentes dentro del turno (FAQ reales de la librería)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.contexto import mensajes_iniciales
from app.servicios.faq import IndiceFAQ
from app.servicios.rag import BuscadorDocumentos
from tests.conftest import crear_ajustes, crear_servicio
from tests.dobles import ModeloFalso


@pytest.mark.parametrize(
    ("mensaje", "faq_id"),
    [
        ("¿cuánto cuesta el envío?", "FAQ-002"),
        ("cuanto cuesta el envio", "FAQ-002"),
        ("¿hacen envíos a provincias?", "FAQ-003"),
        ("quiero devolver un libro", "FAQ-004"),
        ("¿aceptan tarjeta?", "FAQ-001"),
        ("¿dónde están ubicados?", "FAQ-006"),
        ("¿tienen libro de reclamaciones?", "FAQ-010"),
        ("¿envuelven los libros para regalo?", "FAQ-008"),
    ],
)
def test_preguntas_del_negocio_van_a_la_faq(
    servicio: ServicioChat, mensaje: str, faq_id: str
) -> None:
    r = servicio.responder(mensaje, "alumno", "s-faq")
    assert r.ruta == "faq"
    assert r.faq_id == faq_id
    assert r.modo == "sin_llm"


@pytest.mark.parametrize(
    "mensaje",
    ["hola, buenas tardes", "¿tienen libros de fantasía?", "quiero un libro para aprender a sumar"],
)
def test_lo_que_no_es_faq_va_al_modelo(servicio: ServicioChat, mensaje: str) -> None:
    r = servicio.responder(mensaje, "alumno", "s-no-faq")
    assert r.ruta != "faq"
    assert r.clasificacion is not None and r.clasificacion["ruta"] == "llm"


def test_buscar_un_libro_no_cae_en_la_faq_de_devoluciones(servicio: ServicioChat) -> None:
    """'quiero un libro para…' se parece a 'quiero devolver un libro', pero es una búsqueda."""
    r = servicio.responder("quiero un libro para aprender a sumar", "alumno", "s-busca")
    assert r.ruta != "faq"
    assert r.clasificacion is not None and "busqueda" in r.clasificacion["motivo"]
    assert servicio.responder("quiero devolver un libro", "a", "s-dev").faq_id == "FAQ-004"


def test_peticion_de_accion_no_se_responde_con_faq(servicio_con_modelo: ServicioChat) -> None:
    for mensaje in ("págalo con tarjeta", "¿cómo va PED-1A2B3C4D?", "lo quiero"):
        r = servicio_con_modelo.responder(mensaje, "alumno", "s-accion")
        assert r.ruta != "faq"
        assert r.clasificacion is not None and "accion" in r.clasificacion["motivo"]


def test_la_clasificacion_queda_en_la_respuesta(servicio: ServicioChat) -> None:
    r = servicio.responder("¿hacen envíos a domicilio en Lima?", "alumno", "s-faq")
    assert r.clasificacion is not None
    for clave in ("ruta", "similitud", "similitud_segunda", "motivo", "candidatos"):
        assert clave in r.clasificacion


def test_umbral_configurable(
    tmp_path: Path, bd: BaseDatos, registro: RegistroEventos, buscador: BuscadorDocumentos
) -> None:
    """Con el umbral por encima de la similitud real, la FAQ deja de ganar."""
    ajustes = crear_ajustes(tmp_path, faq_umbral=0.99)
    s = crear_servicio(ajustes, registro, bd, buscador, ModeloFalso(fallar=True))
    r = s.responder("¿el envío a Lima tiene algún costo extra los domingos?", "alumno", "s-u")
    assert r.ruta != "faq"


def test_el_contexto_de_faq_llega_al_modelo(servicio_con_modelo: ServicioChat) -> None:
    """Si la FAQ no gana pero se parece, el modelo la recibe como contexto verificado.

    Va al final del mensaje del cliente y no en el de sistema: así el prefijo del prompt
    no cambia entre turnos y el proveedor lo sirve de su caché.
    """
    mensaje = "quiero comprar con factura, ¿hacen factura con RUC?"  # la acción va al modelo
    servicio_con_modelo.responder(mensaje, "a", "s-ctx")
    mensajes = servicio_con_modelo.llm.llamadas[0]["mensajes"]  # type: ignore[attr-defined]
    assert "Preguntas frecuentes" not in mensajes[0]["content"]
    assert mensajes[0]["content"] == servicio_con_modelo.prompt_sistema
    assert mensajes[-1]["content"].startswith(mensaje)
    assert "preguntas frecuentes verificadas" in mensajes[-1]["content"]
    guardado = servicio_con_modelo.bd.mensajes_de("s-ctx")[0]["contenido_redactado"]
    assert "preguntas frecuentes" not in guardado  # el historial guarda solo lo que escribió


def test_una_base_de_faqs_vacia_es_un_error() -> None:
    with pytest.raises(ValueError, match="vacía"):
        IndiceFAQ([], [])


@pytest.mark.parametrize(
    ("mensaje", "es_busqueda"),
    [
        ("quiero un libro para que mi hijo aprenda a sumar", True),
        ("¿tienen libros de fantasía?", True),
        ("recomiéndame algo para leer", True),
        ("busco algo parecido a El hobbit", True),
        ("una novela de misterio", True),
        ("quiero un libro para regalo de mi hermana", True),
        ("¿tienen libro de reclamaciones?", False),
        ("quiero devolver un libro", False),
        ("¿puedo cambiar un libro por otro?", False),
        ("¿envuelven los libros para regalo?", False),
        ("¿cuánto cuesta el envío?", False),
    ],
)
def test_patron_de_busqueda_de_libros(mensaje: str, es_busqueda: bool) -> None:
    from app.servicios.turno import RE_BUSQUEDA

    assert bool(RE_BUSQUEDA.search(mensaje)) is es_busqueda


def test_una_faq_poco_parecida_no_se_adjunta_al_pedido_de_un_libro(
    servicio_con_modelo: ServicioChat,
) -> None:
    """Una FAQ de devoluciones con similitud 0.30 desviaba "quiero ciencia ficción con robots"."""
    mensaje = "quiero ciencia ficción con robots que investigan crímenes"
    sin_minimo = mensajes_iniciales(
        "P", servicio_con_modelo.faq, servicio_con_modelo.bd, mensaje, "s", saludo=False, limite=8
    )
    con_minimo = mensajes_iniciales(
        "P", servicio_con_modelo.faq, servicio_con_modelo.bd, mensaje, "s",
        saludo=False, limite=8, faq_minimo=0.5,
    )  # fmt: skip
    assert "preguntas frecuentes" in sin_minimo[-1]["content"]
    assert con_minimo[-1]["content"] == mensaje
