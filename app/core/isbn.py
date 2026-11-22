"""ISBN-13: normalización y dígito verificador.

Vive en `core` porque lo usan tanto el catálogo como la seguridad: un ISBN tiene
13 dígitos seguidos y no debe confundirse con un número de tarjeta.
"""

from __future__ import annotations

import re

RE_SEPARADORES = re.compile(r"[\s-]+")
# Un ISBN-13 escrito en un texto: 978 o 979 y diez dígitos más, con o sin guiones o espacios.
RE_ISBN = re.compile(r"\b97[89](?:[-\s]?\d){10}\b")


def normalizar_isbn(valor: str) -> str:
    """'978-612-00-0001-7' -> '9786120000017' (sin guiones ni espacios)."""
    return RE_SEPARADORES.sub("", str(valor)).upper()


def isbns_en(texto: str) -> list[str]:
    """ISBN-13 válidos mencionados en un texto, normalizados y sin repetir."""
    candidatos = (normalizar_isbn(m.group(0)) for m in RE_ISBN.finditer(texto or ""))
    return [isbn for isbn in dict.fromkeys(candidatos) if isbn13_valido(isbn)]


def isbn13_valido(isbn: str) -> bool:
    """13 dígitos, prefijo 978 o 979 y dígito verificador correcto."""
    if len(isbn) != 13 or not isbn.isdigit() or not isbn.startswith(("978", "979")):
        return False
    suma = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(isbn[:12]))
    return (10 - suma % 10) % 10 == int(isbn[12])
