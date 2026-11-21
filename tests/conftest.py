"""Entorno de pruebas aislado: base de datos y logs en una carpeta temporal, sin red."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import RAIZ, Settings
from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.faq import IndiceFAQ
from app.servicios.rag import BuscadorDocumentos, Chunk, ParametrosRAG
from app.servicios.turno import AjustesChat
from tests.dobles import ModeloFalso

DATOS = RAIZ / "datos"


def crear_ajustes(tmp: Path, **extra: object) -> Settings:
    """Configuración de prueba: ignora el `.env` y cualquier clave del entorno."""
    base: dict[str, object] = {
        "entorno": "pruebas",
        "db_path": tmp / "tienda.db",
        "log_archivo": tmp / "eventos.jsonl",
        "cache_embeddings": tmp / "embeddings.npz",
        "log_consola": False,
        "llm_base_url": "",
        "llm_api_key": "",
        "llm_model": "",
        "llm_2_base_url": "",
        "embeddings_base_url": "",
        "embeddings_api_key": "",
        "admin_token": "",
        "limite_chat_por_minuto": 0,
    }
    base.update(extra)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


@pytest.fixture()
def ajustes(tmp_path: Path) -> Settings:
    return crear_ajustes(tmp_path)


@pytest.fixture()
def registro(ajustes: Settings) -> RegistroEventos:
    return RegistroEventos.desde_config(ajustes)


@pytest.fixture()
def bd(ajustes: Settings) -> BaseDatos:
    base = BaseDatos(ajustes.db_path, DATOS / "catalogo.json")
    base.inicializar(recargar_catalogo=True)
    return base


def chunks_de_prueba() -> list[Chunk]:
    return [
        Chunk(
            "doc-garantia",
            "politicas.pdf",
            1,
            0,
            "La garantía cubre defectos de fabricación durante un año desde la fecha de "
            "entrega del producto.",
        ),
        Chunk(
            "doc-envio",
            "politicas.pdf",
            2,
            1,
            "El envío es gratis para compras superiores a doscientos dólares y demora de dos "
            "a cinco días hábiles.",
        ),
        Chunk(
            "doc-devolucion",
            "manual.pdf",
            3,
            0,
            "Para devolver un producto debe estar sin uso, con su empaque original y dentro de "
            "los treinta días desde la entrega.",
        ),
        Chunk(
            "doc-monitor",
            "manual.pdf",
            4,
            1,
            "Para conectar un monitor externo use el cable VGA o HDMI y presione fn más f2 para "
            "alternar la imagen de la pantalla.",
        ),
    ]


@pytest.fixture()
def buscador() -> BuscadorDocumentos:
    return BuscadorDocumentos(chunks_de_prueba(), ParametrosRAG())


def faq_de(ajustes: Settings) -> IndiceFAQ:
    return IndiceFAQ.desde_archivo(
        DATOS / "faq.json",
        umbral=ajustes.faq_umbral,
        margen=ajustes.faq_margen,
        umbral_contexto=ajustes.faq_contexto_min,
    )


def crear_servicio(
    ajustes: Settings,
    registro: RegistroEventos,
    bd: BaseDatos,
    buscador: BuscadorDocumentos,
    modelo: ModeloFalso,
) -> ServicioChat:
    return ServicioChat(
        bd=bd,
        registro=registro,
        buscador=buscador,
        llm=modelo,
        faq=faq_de(ajustes),
        prompt_sistema=(DATOS / "prompt_sistema.md").read_text(encoding="utf-8"),
        ajustes=AjustesChat.desde_config(ajustes),
    )


@pytest.fixture()
def servicio(
    ajustes: Settings, registro: RegistroEventos, bd: BaseDatos, buscador: BuscadorDocumentos
) -> ServicioChat:
    """Servicio cuyo modelo no está configurado: cae a modo demo."""
    return crear_servicio(ajustes, registro, bd, buscador, ModeloFalso(fallar=True))


@pytest.fixture()
def modelo() -> ModeloFalso:
    return ModeloFalso()


@pytest.fixture()
def servicio_con_modelo(
    ajustes: Settings,
    registro: RegistroEventos,
    bd: BaseDatos,
    buscador: BuscadorDocumentos,
    modelo: ModeloFalso,
) -> ServicioChat:
    """El modelo responde lo que cada prueba prepare en `modelo.respuestas`."""
    return crear_servicio(ajustes, registro, bd, buscador, modelo)
