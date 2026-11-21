"""Límite de peticiones y validaciones de la configuración."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import __version__
from app.api.limitador import LimitadorPeticiones
from app.core.config import RAIZ
from tests.conftest import crear_ajustes


class Reloj:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_limitador_ventana_deslizante() -> None:
    reloj = Reloj()
    limitador = LimitadorPeticiones(2, 60, reloj=reloj)
    assert limitador.permitir("ip-1") == (True, 0)
    assert limitador.permitir("ip-1") == (True, 0)
    permitido, espera = limitador.permitir("ip-1")
    assert not permitido and espera == 60
    assert limitador.permitir("ip-2")[0]  # otro cliente no se ve afectado
    reloj.t = 61
    assert limitador.permitir("ip-1")[0]  # la ventana se liberó


def test_limitador_en_cero_esta_desactivado() -> None:
    limitador = LimitadorPeticiones(0)
    assert all(limitador.permitir("ip")[0] for _ in range(100))


def test_config_rechaza_valores_fuera_de_rango(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        crear_ajustes(tmp_path, rag_mmr_lambda=1.5)
    with pytest.raises(ValidationError):
        crear_ajustes(tmp_path, chunk_max_palabras=50, chunk_overlap_palabras=60)


def test_produccion_exige_admin_token(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="ADMIN_TOKEN"):
        crear_ajustes(tmp_path, entorno="produccion", admin_token="")
    assert crear_ajustes(tmp_path, entorno="produccion", admin_token="x").entorno == "produccion"


def test_rutas_relativas_se_resuelven_desde_la_raiz(tmp_path: Path) -> None:
    ajustes = crear_ajustes(tmp_path, db_path="var/otra.db")
    assert ajustes.db_path == RAIZ / "var" / "otra.db"


def test_url_base_sin_barra_final(tmp_path: Path) -> None:
    ajustes = crear_ajustes(tmp_path, llm_base_url="https://api.ejemplo.com/v1/")
    assert ajustes.llm_base_url == "https://api.ejemplo.com/v1"


def test_la_version_coincide_con_pyproject() -> None:
    proyecto = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    assert proyecto["project"]["version"] == __version__


def test_el_limitador_olvida_clientes_inactivos() -> None:
    reloj = Reloj()
    limitador = LimitadorPeticiones(5, 60, reloj=reloj, max_clientes=2)
    limitador.permitir("a")
    limitador.permitir("b")
    reloj.t = 120
    limitador.permitir("c")  # supera el máximo: se purgan los inactivos
    assert set(limitador._marcas) == {"c"}


def test_k_final_no_puede_superar_k_inicial(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="RAG_K_FINAL"):
        crear_ajustes(tmp_path, rag_k_inicial=2, rag_k_final=3)


def test_llm_configurado_requiere_url_clave_y_modelo(tmp_path: Path) -> None:
    assert not crear_ajustes(tmp_path, llm_base_url="https://x.test", llm_model="m").llm_configurado
    completo = crear_ajustes(
        tmp_path, llm_base_url="https://x.test", llm_api_key="k", llm_model="m"
    )
    assert completo.llm_configurado
