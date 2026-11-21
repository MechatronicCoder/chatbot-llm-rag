"""Regla del proyecto: ningún archivo de código supera las 400 líneas.

Un archivo largo suele mezclar responsabilidades y cuesta leerlo y revisarlo.
Si un archivo se acerca al límite, se divide por responsabilidad (ver cómo
están separados `app/persistencia/` o `frontend/css/`).

Uso:
    uv run python scripts/verificar_lineas.py              # todos los archivos versionados
    uv run python scripts/verificar_lineas.py a.py b.js    # solo los indicados (pre-commit)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MAXIMO = 400
EXTENSIONES = {".py", ".js", ".css", ".html", ".bicep", ".yml", ".yaml", ".sh", ".ps1", ".toml"}
NOMBRES = {"Dockerfile"}
EXCLUIDOS = {"uv.lock"}  # generado automáticamente, no se edita a mano


def es_codigo(ruta: Path) -> bool:
    return ruta.name not in EXCLUIDOS and (ruta.suffix in EXTENSIONES or ruta.name in NOMBRES)


def archivos_versionados() -> list[Path]:
    salida = subprocess.run(
        ["git", "ls-files"],  # noqa: S607 - git del PATH, sin entrada del usuario
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [Path(linea) for linea in salida.splitlines()]


def main(argumentos: list[str]) -> int:
    rutas = [Path(a) for a in argumentos] if argumentos else archivos_versionados()
    excedidos = []
    for ruta in sorted(r for r in rutas if es_codigo(r) and r.is_file()):
        lineas = len(ruta.read_text(encoding="utf-8").splitlines())
        if lineas > MAXIMO:
            excedidos.append((ruta, lineas))

    for ruta, lineas in excedidos:
        print(f"{ruta.as_posix()}: {lineas} líneas (máximo {MAXIMO})")
    if excedidos:
        print(f"\n{len(excedidos)} archivo(s) superan el máximo; dividirlos por responsabilidad.")
        return 1
    print(f"Ningún archivo de código supera las {MAXIMO} líneas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
