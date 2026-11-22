"""Normalización de texto compartida: comparar sin mayúsculas, tildes ni signos."""

from __future__ import annotations

import re
import unicodedata


def sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y sin signos: 'Fantasía!' -> 'fantasia'."""
    return re.sub(r"[^a-z0-9ñ ]+", " ", sin_acentos(texto.lower())).strip()
