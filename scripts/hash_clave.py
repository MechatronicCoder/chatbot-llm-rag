"""Genera el hash de la clave de acceso para ACCESO_CLAVE_HASH.

Pide la clave dos veces sin mostrarla y escribe la línea lista para el `.env`.
La clave no se guarda en ningún lado; en Azure el hash va como secreto.

Uso:
    uv run python scripts/hash_clave.py
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.claves import generar_hash

MINIMO = 10


def main() -> int:
    clave = getpass.getpass("Clave de acceso: ")
    if len(clave) < MINIMO:
        print(f"La clave debe tener al menos {MINIMO} caracteres.")
        return 1
    if getpass.getpass("Repite la clave: ") != clave:
        print("Las claves no coinciden.")
        return 1
    # Comillas simples: Docker Compose no interpreta los "$" del hash como variables.
    print(f"ACCESO_CLAVE_HASH='{generar_hash(clave)}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
