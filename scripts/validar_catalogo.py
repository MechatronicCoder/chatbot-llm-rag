"""Valida un archivo de catálogo antes de subirlo o desplegarlo.

Revisa cada libro con el mismo esquema que usa la aplicación al arrancar e
informa todos los problemas de una vez (libro, ISBN y campo), más advertencias
de datos opcionales que faltan y un resumen por categoría.

Uso:
    uv run python scripts/validar_catalogo.py                     # datos/catalogo.json
    uv run python scripts/validar_catalogo.py ruta/otro.json      # otro archivo
    uv run python scripts/validar_catalogo.py --estricto          # advertencias = error

Sale con código 1 si hay errores (o advertencias con --estricto).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from app.catalogo.carga import ErrorCatalogo, advertencias, cargar_catalogo  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("ruta", nargs="?", type=Path, default=RAIZ / "datos" / "catalogo.json")
    parser.add_argument(
        "--estricto", action="store_true", help="las advertencias cuentan como error"
    )
    args = parser.parse_args()

    try:
        libros = cargar_catalogo(args.ruta)
    except ErrorCatalogo as error:
        print(f"Catálogo inválido: {args.ruta}")
        for problema in error.problemas:
            print(f"  ERROR  {problema}")
        return 1

    avisos = advertencias(libros)
    for aviso in avisos:
        print(f"  AVISO  {aviso}")

    categorias = Counter(libro.categoria for libro in libros)
    sin_stock = sum(1 for libro in libros if libro.stock == 0)
    print(f"\nCatálogo válido: {len(libros)} libros en {len(categorias)} categorías.")
    for categoria, cantidad in categorias.most_common():
        print(f"  {categoria:<20} {cantidad}")
    print(f"Sin stock: {sin_stock} · con advertencias: {len(avisos)}")
    return 1 if args.estricto and avisos else 0


if __name__ == "__main__":
    raise SystemExit(main())
