"""Entorno de pruebas aislado: datos, base y logs en una carpeta temporal, sin red."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.catalogo.carga import cargar_tienda
from app.core.config import RAIZ, Settings
from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.fabrica import preparar_prompt
from app.servicios.faq import IndiceFAQ
from app.servicios.rag import BuscadorDocumentos, Chunk, ParametrosRAG
from app.servicios.recomendador import Recomendador
from app.servicios.turno import AjustesChat
from tests.datos_prueba import LIBROS_PRUEBA
from tests.dobles import ModeloFalso

DATOS_REALES = RAIZ / "datos"


def crear_datos(destino: Path, libros: list[dict[str, object]] | None = None) -> Path:
    """Carpeta de datos de prueba: FAQ, prompt y tienda reales; catálogo de prueba."""
    destino.mkdir(parents=True, exist_ok=True)
    for archivo in ("faq.json", "prompt_sistema.md", "tienda.json"):
        shutil.copy(DATOS_REALES / archivo, destino / archivo)
    catalogo = {"libros": LIBROS_PRUEBA if libros is None else libros}
    (destino / "catalogo.json").write_text(json.dumps(catalogo, ensure_ascii=False), "utf-8")
    return destino


def crear_ajustes(tmp: Path, **extra: object) -> Settings:
    """Configuración de prueba: ignora el `.env` y cualquier clave del entorno."""
    carpeta = tmp / "datos"
    if not (carpeta / "catalogo.json").exists():
        crear_datos(carpeta)
    base: dict[str, object] = {
        "entorno": "pruebas",
        "carpeta_datos": carpeta,
        "db_path": tmp / "tienda.db",
        "log_archivo": tmp / "eventos.jsonl",
        "cache_embeddings": tmp / "embeddings.npz",
        "cache_embeddings_catalogo": tmp / "embeddings_catalogo.npz",
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


def crear_bd(ajustes: Settings) -> BaseDatos:
    base = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    base.inicializar(recargar_catalogo=True)
    return base


@pytest.fixture()
def bd(ajustes: Settings) -> BaseDatos:
    return crear_bd(ajustes)


def chunks_de_prueba() -> list[Chunk]:
    return [
        Chunk(
            "doc-envio",
            "politicas.md",
            1,
            0,
            "El envío en Lima cuesta diez soles y es gratis en compras desde ciento veinte soles.",
        ),
        Chunk(
            "doc-cambios",
            "politicas.md",
            2,
            0,
            "Un libro se puede cambiar dentro de los siete días si está nuevo y sin uso, "
            "con su comprobante de pago.",
        ),
        Chunk(
            "doc-defectos",
            "politicas.md",
            3,
            0,
            "Si el libro llega con páginas en blanco o mal impresas se cambia sin costo "
            "dentro de los treinta días.",
        ),
        Chunk(
            "doc-reclamos",
            "manual.pdf",
            4,
            1,
            "El libro de reclamaciones permite registrar un reclamo; el proveedor responde "
            "en un plazo máximo de quince días hábiles.",
        ),
    ]


@pytest.fixture()
def buscador() -> BuscadorDocumentos:
    return BuscadorDocumentos(chunks_de_prueba(), ParametrosRAG())


def faq_de(ajustes: Settings) -> IndiceFAQ:
    return IndiceFAQ.desde_archivo(
        ajustes.carpeta_datos / "faq.json",
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
    tienda = cargar_tienda(ajustes.carpeta_datos / "tienda.json")
    plantilla = (ajustes.carpeta_datos / "prompt_sistema.md").read_text(encoding="utf-8")
    return ServicioChat(
        bd=bd,
        registro=registro,
        buscador=buscador,
        recomendador=Recomendador(bd.libros_activos()),
        llm=modelo,
        faq=faq_de(ajustes),
        prompt_sistema=preparar_prompt(plantilla, tienda),
        tienda=tienda,
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
