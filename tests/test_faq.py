"""Clasificador de preguntas frecuentes dentro del turno."""

from __future__ import annotations

from pathlib import Path

from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.rag import BuscadorDocumentos
from tests.conftest import crear_ajustes, crear_servicio
from tests.dobles import ModeloFalso


def test_pregunta_de_negocio_va_a_la_faq(servicio: ServicioChat) -> None:
    r = servicio.responder("¿cuánto cuesta el envío?", "alumno", "s-faq")
    assert r.ruta == "faq"
    assert r.faq_id == "FAQ-002"
    assert r.modo == "sin_llm"
    assert "envío" in r.respuesta.lower()


def test_garantia_va_a_la_faq(servicio: ServicioChat) -> None:
    r = servicio.responder("¿cuánto dura la garantía?", "alumno", "s-faq")
    assert r.ruta == "faq"
    assert r.faq_id


def test_saludo_no_es_faq(servicio: ServicioChat) -> None:
    r = servicio.responder("hola, buenas tardes", "alumno", "s-faq")
    assert r.ruta != "faq"
    assert r.clasificacion is not None and r.clasificacion["ruta"] == "llm"


def test_peticion_de_accion_no_se_responde_con_faq(servicio_con_modelo: ServicioChat) -> None:
    r = servicio_con_modelo.responder("págala con tarjeta", "alumno", "s-accion")
    assert r.ruta != "faq"
    assert r.clasificacion is not None and "accion" in r.clasificacion["motivo"]


def test_id_de_pedido_fuerza_el_modelo(servicio_con_modelo: ServicioChat) -> None:
    r = servicio_con_modelo.responder("¿cómo va PED-1A2B3C4D?", "alumno", "s-accion")
    assert r.ruta != "faq"


def test_la_clasificacion_queda_en_la_respuesta(servicio: ServicioChat) -> None:
    r = servicio.responder("¿hacen envíos a domicilio?", "alumno", "s-faq")
    assert r.clasificacion is not None
    for clave in ("ruta", "similitud", "similitud_segunda", "motivo", "candidatos"):
        assert clave in r.clasificacion


def test_umbral_configurable(
    tmp_path: Path, bd: BaseDatos, registro: RegistroEventos, buscador: BuscadorDocumentos
) -> None:
    """Con el umbral por encima de la similitud real, la FAQ deja de ganar."""
    ajustes = crear_ajustes(tmp_path, faq_umbral=0.99)
    s = crear_servicio(ajustes, registro, bd, buscador, ModeloFalso(fallar=True))
    r = s.responder("¿el envío tiene algún costo extra para provincia?", "alumno", "s-umbral")
    assert r.ruta != "faq"


def test_el_contexto_de_faq_llega_al_modelo(servicio_con_modelo: ServicioChat) -> None:
    """Si la FAQ no gana pero se parece, el modelo la recibe como contexto verificado."""
    servicio_con_modelo.responder("¿y si quiero pagar en cuotas sin intereses?", "a", "s-ctx")
    sistema = servicio_con_modelo.llm.llamadas[0]["mensajes"][0]["content"]  # type: ignore[attr-defined]
    assert "Preguntas frecuentes verificadas" in sistema


def test_una_base_de_faqs_vacia_es_un_error() -> None:
    import pytest

    from app.servicios.faq import IndiceFAQ

    with pytest.raises(ValueError, match="vacía"):
        IndiceFAQ([], [])
