"""Esquema de un libro del catálogo y sus validaciones.

Cada libro del archivo `datos/catalogo.json` se valida con este modelo al
arrancar: un ISBN mal escrito, un rango de edad al revés o una valoración sin
votos detienen la carga con un mensaje que indica el libro y el campo.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.isbn import isbn13_valido, normalizar_isbn


def _isbn_o_error(valor: str) -> str:
    isbn = normalizar_isbn(valor)
    if not isbn13_valido(isbn):
        raise ValueError(f"ISBN-13 inválido: {valor!r} (13 dígitos, prefijo 978/979, verificador)")
    return isbn


class Libro(BaseModel):
    """Un libro a la venta. Solo `isbn`, `titulo`, `autor`, `categoria`, `precio` y
    `stock` son obligatorios; el resto mejora la búsqueda y la recomendación."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # identidad
    isbn: str
    titulo: str = Field(min_length=1, max_length=300)
    autor: str = Field(min_length=1, max_length=200)
    editorial: str = Field(default="", max_length=120)
    anio: int | None = Field(default=None, ge=1450, le=2100)
    # clasificación
    categoria: str = Field(min_length=1, max_length=40)
    genero: str = Field(default="", max_length=60)
    temas: list[str] = Field(default_factory=list, max_length=20)
    protagonista: str = Field(default="", max_length=200)
    tono: str = Field(default="", max_length=80)
    edad_min: int | None = Field(default=None, ge=0, le=99)
    edad_max: int | None = Field(default=None, ge=0, le=99)
    # ficha
    sinopsis: str = Field(default="", max_length=1500)
    formato: str = Field(default="", max_length=40)
    paginas: int | None = Field(default=None, ge=1, le=10000)
    idioma: str = Field(default="español", max_length=40)
    # recomendación
    valoracion: float | None = Field(default=None, ge=0, le=5)
    num_valoraciones: int = Field(default=0, ge=0)
    similares: list[str] = Field(default_factory=list, max_length=10)
    # venta
    precio: float = Field(gt=0, le=100000)
    stock: int = Field(ge=0)
    portada: str | None = Field(default=None, max_length=300)

    @field_validator("isbn")
    @classmethod
    def _isbn(cls, valor: str) -> str:
        return _isbn_o_error(valor)

    @field_validator("similares")
    @classmethod
    def _similares(cls, valores: list[str]) -> list[str]:
        return list(dict.fromkeys(_isbn_o_error(v) for v in valores))

    @field_validator("categoria", "genero", "tono", "idioma")
    @classmethod
    def _minusculas(cls, valor: str) -> str:
        return valor.lower()

    @field_validator("temas")
    @classmethod
    def _temas(cls, valores: list[str]) -> list[str]:
        limpios = (v.strip().lower() for v in valores)
        return list(dict.fromkeys(v for v in limpios if v))

    @field_validator("precio")
    @classmethod
    def _precio(cls, valor: float) -> float:
        """Dinero: dos decimales redondeando la mitad hacia arriba (10.555 -> 10.56)."""
        return float(Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @field_validator("portada")
    @classmethod
    def _portada(cls, valor: str | None) -> str | None:
        if valor is None or valor == "":
            return None
        if ".." in valor or valor.startswith(("/", "\\")) or "://" in valor:
            raise ValueError("portada debe ser una ruta relativa dentro de frontend/ (sin URL)")
        return valor

    @model_validator(mode="after")
    def _coherencia(self) -> Libro:
        if (
            self.edad_min is not None
            and self.edad_max is not None
            and self.edad_min > self.edad_max
        ):
            raise ValueError(f"edad_min ({self.edad_min}) es mayor que edad_max ({self.edad_max})")
        if self.valoracion is None and self.num_valoraciones:
            raise ValueError("hay num_valoraciones pero falta valoracion")
        if self.valoracion is not None and not self.num_valoraciones:
            raise ValueError("una valoracion necesita num_valoraciones mayor que 0")
        if self.isbn in self.similares:
            raise ValueError("un libro no puede ser similar a sí mismo")
        return self

    # -- vistas ---------------------------------------------------------------
    def texto_para_busqueda(self) -> str:
        return texto_para_busqueda(self.model_dump())

    def a_fila(self) -> dict[str, Any]:
        """Diccionario listo para SQLite (las listas se guardan como JSON)."""
        datos = self.model_dump()
        datos["temas"] = json.dumps(self.temas, ensure_ascii=False)
        datos["similares"] = json.dumps(self.similares)
        return datos


def texto_para_busqueda(libro: Mapping[str, Any]) -> str:
    """Texto que se vectoriza para recomendar: lo que describe al libro, no su precio."""
    partes = [
        f"{libro['titulo']}. {libro['autor']}.",
        f"Categoría: {libro['categoria']}." if libro.get("categoria") else "",
        f"Género: {libro['genero']}." if libro.get("genero") else "",
        f"Temas: {', '.join(libro['temas'])}." if libro.get("temas") else "",
        f"Protagonista: {libro['protagonista']}." if libro.get("protagonista") else "",
        f"Tono: {libro['tono']}." if libro.get("tono") else "",
        libro.get("sinopsis") or "",
    ]
    return " ".join(p for p in partes if p)
