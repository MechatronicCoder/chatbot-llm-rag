"""Arranque: fábrica del servicio real, estado compartido, ciclo de vida y configuración."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as modulo_main
from app.api.dependencias import EstadoApp
from app.core.config import get_settings
from app.core.logs import RegistroEventos
from app.llm.cliente import ClienteLLM
from app.llm.embeddings import ClienteEmbeddings
from app.main import crear_app
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat, construir_servicio
from app.servicios.rag import BuscadorDocumentos
from tests.conftest import chunks_de_prueba, crear_ajustes, crear_servicio
from tests.dobles import ModeloFalso


def test_construir_servicio_con_los_datos_reales(tmp_path: Path, bd: BaseDatos) -> None:
    ajustes = crear_ajustes(
        tmp_path,
        llm_base_url="https://llm.test/v1",
        llm_api_key="clave",
        llm_model="modelo",
        embeddings_base_url="https://emb.test/v1",
        embeddings_api_key="clave",
    )
    servicio = construir_servicio(ajustes, RegistroEventos(None, consola=False), bd)
    assert isinstance(servicio.llm, ClienteLLM) and servicio.llm.configurado
    assert isinstance(servicio.buscador.embedder, ClienteEmbeddings)
    assert len(servicio.buscador.chunks) > 100
    assert "TechStore" in servicio.prompt_sistema
    assert len(servicio.faq.faqs) == 10


def test_sin_claves_el_servicio_no_tiene_embedder(tmp_path: Path, bd: BaseDatos) -> None:
    servicio = construir_servicio(crear_ajustes(tmp_path), RegistroEventos(None), bd)
    assert servicio.buscador.embedder is None
    assert not servicio.llm.configurado


class ClienteCerrable:
    def __init__(self) -> None:
        self.cerrado = False

    def cerrar(self) -> None:
        self.cerrado = True


def armar_estado(tmp_path: Path, fabrica: object) -> EstadoApp:
    ajustes = crear_ajustes(tmp_path)
    registro = RegistroEventos.desde_config(ajustes)
    bd = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    bd.inicializar()
    return EstadoApp(ajustes, registro, bd, fabrica=fabrica)  # type: ignore[arg-type]


def servicio_de_prueba(tmp_path: Path) -> ServicioChat:
    ajustes = crear_ajustes(tmp_path)
    registro = RegistroEventos.desde_config(ajustes)
    bd = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    bd.inicializar()
    return crear_servicio(
        ajustes, registro, bd, BuscadorDocumentos(chunks_de_prueba()), ModeloFalso()
    )


def test_estado_construye_una_vez_e_indexa(tmp_path: Path) -> None:
    construidos: list[ServicioChat] = []

    def fabrica() -> ServicioChat:
        construidos.append(servicio_de_prueba(tmp_path))
        return construidos[-1]

    estado = armar_estado(tmp_path, fabrica)
    assert not estado.listo
    estado.preparar()
    assert estado.listo
    assert estado.obtener_servicio() is construidos[0]
    assert len(construidos) == 1
    assert any(e["evento"] == "corpus.listo" for e in estado.registro.leer())


def test_un_error_al_preparar_queda_en_el_log(tmp_path: Path) -> None:
    def fabrica() -> ServicioChat:
        raise FileNotFoundError("faq.json")

    estado = armar_estado(tmp_path, fabrica)
    estado.preparar()
    assert not estado.listo
    assert any(e["evento"] == "corpus.error" for e in estado.registro.leer(nivel="ERROR"))


def test_cerrar_libera_los_clientes_http(tmp_path: Path) -> None:
    estado = armar_estado(tmp_path, lambda: servicio_de_prueba(tmp_path))
    estado.cerrar()  # sin servicio construido no hace nada

    servicio = estado.obtener_servicio()
    llm, embedder = ClienteCerrable(), ClienteCerrable()
    servicio.llm = llm  # type: ignore[assignment]
    servicio.buscador.embedder = embedder  # type: ignore[assignment]
    estado.cerrar()
    assert llm.cerrado and embedder.cerrado

    servicio.buscador.embedder = None  # un cliente sin `cerrar` se ignora
    estado.cerrar()


def test_el_ciclo_de_vida_indexa_en_segundo_plano(tmp_path: Path) -> None:
    servicio = servicio_de_prueba(tmp_path)
    app = crear_app(crear_ajustes(tmp_path), servicio=servicio, indexar_al_iniciar=True)
    with TestClient(app) as cliente:
        for _ in range(100):
            if cliente.get("/api/health/ready").status_code == 200:
                break
            time.sleep(0.05)
        assert cliente.get("/api/health/ready").status_code == 200
    eventos = [e["evento"] for e in servicio.registro.leer()]
    assert "app.inicio" in eventos


def test_sin_carpeta_de_frontend_la_api_funciona(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(modulo_main, "CARPETA_FRONTEND", tmp_path / "no-existe")
    cliente = TestClient(crear_app(crear_ajustes(tmp_path), indexar_al_iniciar=False))
    assert cliente.get("/").status_code == 404
    assert cliente.get("/api/health/live").status_code == 200


def test_health_mientras_el_servicio_no_existe(tmp_path: Path) -> None:
    cliente = TestClient(crear_app(crear_ajustes(tmp_path), indexar_al_iniciar=False))
    datos = cliente.get("/api/health").json()
    assert datos["estado"] == "iniciando"
    assert datos["corpus"]["indexado"] is False
    assert datos["modo"] == "demo"


def test_get_settings_lee_una_sola_vez() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_ciclo_de_vida_sin_indexar_al_iniciar(tmp_path: Path) -> None:
    with TestClient(crear_app(crear_ajustes(tmp_path), indexar_al_iniciar=False)) as cliente:
        assert cliente.get("/api/health/ready").status_code == 503
