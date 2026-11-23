"""Contrato de los datos del negocio en `datos/`: deben cargar sin errores.

Estas pruebas no dependen del contenido concreto del catálogo (que se reemplaza
por el de la librería), solo de que el archivo sea válido y coherente.
"""

from __future__ import annotations

import json

from app.catalogo.carga import cargar_catalogo, cargar_tienda
from app.servicios.fabrica import preparar_prompt
from app.servicios.faq import IndiceFAQ
from tests.conftest import DATOS_REALES


def test_el_catalogo_real_es_valido() -> None:
    libros = cargar_catalogo(DATOS_REALES / "catalogo.json")
    assert libros
    assert len({lb.isbn for lb in libros}) == len(libros)
    assert any(lb.stock > 0 for lb in libros)


def test_la_tienda_y_el_prompt_se_completan() -> None:
    tienda = cargar_tienda(DATOS_REALES / "tienda.json")
    plantilla = (DATOS_REALES / "prompt_sistema.md").read_text(encoding="utf-8")
    assert "{tienda}" in plantilla and "{moneda}" in plantilla
    prompt = preparar_prompt(plantilla, tienda)
    assert tienda.nombre in prompt and tienda.moneda.simbolo in prompt
    assert "{" not in prompt


def test_las_faq_reales_son_coherentes() -> None:
    banco = json.loads((DATOS_REALES / "faq.json").read_text(encoding="utf-8"))
    ids = [f["id"] for f in banco["faqs"]]
    assert len(ids) == len(set(ids))
    assert all(f["variaciones"] and f["respuesta"] for f in banco["faqs"])
    indice = IndiceFAQ.desde_archivo(
        DATOS_REALES / "faq.json", umbral=0.55, margen=0.03, umbral_contexto=0.25
    )
    for faq in indice.faqs:  # cada FAQ se reconoce a sí misma
        decision = indice.clasificar(faq.pregunta)
        assert decision.ruta == "faq" and decision.faq is not None
        assert decision.faq.id == faq.id


def test_hay_documentos_para_el_rag() -> None:
    documentos = {p.name for p in (DATOS_REALES / "documentos").iterdir()}
    assert "politicas_libreria.md" in documentos
    assert any(nombre.endswith(".pdf") for nombre in documentos)


def test_las_tarifas_de_envio_coinciden_con_las_politicas() -> None:
    """El código cobra lo que dice `tienda.json`; el documento de políticas debe decir lo mismo."""
    tienda = cargar_tienda(DATOS_REALES / "tienda.json")
    politicas = (DATOS_REALES / "documentos" / "politicas_libreria.md").read_text(encoding="utf-8")
    for tarifa in tienda.envio.values():
        assert f"S/ {tarifa.costo.normalize():f}" in politicas
        assert (
            tarifa.gratis_desde is not None
            and f"S/ {tarifa.gratis_desde.normalize():f}" in politicas
        )
