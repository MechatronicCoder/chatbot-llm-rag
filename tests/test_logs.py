"""Registro de eventos: formatos de consola, niveles, rotación, lectura y fallos de disco."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.core.logs import RegistroEventos


def test_consola_en_texto_legible(tmp_path: Path) -> None:
    salida = io.StringIO()
    registro = RegistroEventos(tmp_path / "e.jsonl", salida=salida)
    registro.info("turno.fin", "t-1", ruta="faq", vacio="", nada=None)
    linea = salida.getvalue()
    assert "INFO" in linea and "turno.fin" in linea and "turno=t-1" in linea
    assert "ruta=faq" in linea
    assert "vacio=" not in linea and "nada=" not in linea


def test_consola_en_json_para_log_analytics(tmp_path: Path) -> None:
    salida = io.StringIO()
    registro = RegistroEventos(None, formato="json", salida=salida)
    registro.error("corpus.error", motivo="x")
    evento = json.loads(salida.getvalue())
    assert evento["nivel"] == "ERROR" and evento["evento"] == "corpus.error"


def test_el_nivel_minimo_filtra(tmp_path: Path) -> None:
    salida = io.StringIO()
    registro = RegistroEventos(tmp_path / "e.jsonl", nivel="WARN", salida=salida)
    registro.depuracion("detalle")
    registro.info("normal")
    registro.aviso("importante")
    assert salida.getvalue().count("\n") == 1
    assert [e["evento"] for e in registro.leer()] == ["importante"]


def test_sin_archivo_no_hay_lectura(tmp_path: Path) -> None:
    registro = RegistroEventos(None, consola=False)
    registro.info("x")
    assert registro.leer() == []
    assert RegistroEventos(tmp_path / "no-existe.jsonl").leer() == []


def test_rotacion_por_tamano(tmp_path: Path) -> None:
    archivo = tmp_path / "e.jsonl"
    registro = RegistroEventos(archivo, consola=False, rotacion_mb=0.0002)  # ~200 bytes
    for i in range(20):
        registro.info("evento", "t-1", n=i, relleno="x" * 20)
    assert (tmp_path / "e.jsonl.1").exists()
    assert archivo.stat().st_size < 1000


def test_lectura_con_filtros_y_lineas_danadas(tmp_path: Path) -> None:
    archivo = tmp_path / "e.jsonl"
    registro = RegistroEventos(archivo, consola=False)
    registro.info("a", "t-1")
    registro.aviso("b", "t-2")
    with archivo.open("a", encoding="utf-8") as fh:
        fh.write("\n{esto no es json\n")
    registro.error("c", "t-1")

    assert [e["evento"] for e in registro.leer()] == ["c", "b", "a"]
    assert [e["evento"] for e in registro.leer(nivel="WARN")] == ["c", "b"]
    assert [e["evento"] for e in registro.leer(turno="t-1")] == ["c", "a"]
    assert len(registro.leer(limite=1)) == 1


def test_un_disco_con_problemas_no_tumba_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archivo = tmp_path / "e.jsonl"
    registro = RegistroEventos(archivo, consola=False, rotacion_mb=0.000001)
    registro.info("primero")

    def falla(*_: object, **__: object) -> None:
        raise OSError("disco lleno")

    monkeypatch.setattr(Path, "rename", falla)
    monkeypatch.setattr(Path, "open", falla)
    monkeypatch.setattr(Path, "read_text", falla)
    registro.info("segundo")  # rotar y escribir fallan sin lanzar
    assert registro.leer() == []  # leer falla sin lanzar
