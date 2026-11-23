"""Carga y validación de los datos del negocio: la tienda y su catálogo de libros.

`cargar_catalogo` no se detiene en el primer error: junta todos los problemas
del archivo (libro, ISBN y campo) para corregirlos de una sola vez.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.catalogo.modelo import Libro


class ErrorCatalogo(ValueError):
    """El archivo del catálogo o de la tienda tiene datos inválidos."""

    def __init__(self, problemas: list[str]) -> None:
        self.problemas = problemas
        super().__init__(
            f"{len(problemas)} problema(s) en los datos:\n" + "\n".join(f"- {p}" for p in problemas)
        )


class Moneda(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simbolo: str = Field(min_length=1, max_length=5)
    codigo: str = Field(min_length=3, max_length=3)


class ZonaEnvio(BaseModel):
    """Tarifa de envío de una zona: costo fijo, gratis desde cierto subtotal."""

    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(min_length=1, max_length=60)
    costo: Decimal = Field(ge=0, max_digits=8, decimal_places=2)
    gratis_desde: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)


class Contacto(BaseModel):
    """Cómo hablar con una persona del equipo (lo usa `derivar_a_persona`)."""

    model_config = ConfigDict(extra="forbid")
    telefono: str = Field(min_length=1, max_length=40)
    correo: str = Field(min_length=3, max_length=80)
    horario: str = Field(min_length=1, max_length=120)


class Tienda(BaseModel):
    """Identidad de la tienda: se usa en el prompt, en los textos y en el frontend."""

    model_config = ConfigDict(extra="forbid")
    nombre: str = Field(min_length=1, max_length=80)
    descripcion: str = Field(default="", max_length=300)
    moneda: Moneda
    # Zonas de envío ("lima", "provincias"): el código calcula el envío, no el modelo.
    envio: dict[str, ZonaEnvio] = Field(min_length=1)
    contacto: Contacto | None = None

    def precio(self, valor: float | Decimal) -> str:
        return f"{self.moneda.simbolo} {valor:,.2f}"


def _leer_json(ruta: Path) -> Any:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ErrorCatalogo([f"no existe el archivo {ruta}"]) from exc
    except json.JSONDecodeError as exc:
        raise ErrorCatalogo([f"{ruta.name}: JSON inválido en la línea {exc.lineno}"]) from exc


def _describir(error: ValidationError) -> str:
    detalles = []
    for e in error.errors():
        campo = ".".join(str(p) for p in e["loc"]) or "libro"
        detalles.append(f"{campo}: {e['msg']}")
    return "; ".join(detalles)


def cargar_tienda(ruta: Path) -> Tienda:
    try:
        return Tienda.model_validate(_leer_json(ruta))
    except ValidationError as exc:
        raise ErrorCatalogo([f"{ruta.name}: {_describir(exc)}"]) from exc


def cargar_catalogo(ruta: Path) -> list[Libro]:
    """Valida cada libro, los ISBN repetidos y que `similares` apunte a libros existentes."""
    datos = _leer_json(ruta)
    if not isinstance(datos, dict) or not isinstance(datos.get("libros"), list):
        raise ErrorCatalogo([f"{ruta.name}: se esperaba un objeto con la lista 'libros'"])

    libros: list[Libro] = []
    problemas: list[str] = []
    for posicion, crudo in enumerate(datos["libros"], start=1):
        referencia = f"libro {posicion}"
        if isinstance(crudo, dict) and crudo.get("isbn"):
            referencia += f" (isbn {crudo['isbn']})"
        try:
            libros.append(Libro.model_validate(crudo))
        except ValidationError as exc:
            problemas.append(f"{referencia}: {_describir(exc)}")

    vistos: set[str] = set()
    for libro in libros:
        if libro.isbn in vistos:
            problemas.append(f"isbn {libro.isbn} repetido ({libro.titulo})")
        vistos.add(libro.isbn)
    for libro in libros:
        faltan = [s for s in libro.similares if s not in vistos]
        if faltan:
            problemas.append(
                f"isbn {libro.isbn}: similares no existe en el catálogo: {', '.join(faltan)}"
            )

    if not libros and not problemas:
        problemas.append(f"{ruta.name}: la lista 'libros' está vacía")
    if problemas:
        raise ErrorCatalogo(problemas)
    return libros


def advertencias(libros: list[Libro]) -> list[str]:
    """Datos opcionales que faltan y empeoran la recomendación (no impiden cargar)."""
    avisos = []
    for libro in libros:
        faltan = [
            campo
            for campo, vacio in (
                ("sinopsis", not libro.sinopsis),
                ("temas", not libro.temas),
                ("edad_min", libro.edad_min is None),
                ("valoracion", libro.valoracion is None),
            )
            if vacio
        ]
        if faltan:
            avisos.append(f"isbn {libro.isbn} ({libro.titulo}): sin {', '.join(faltan)}")
    return avisos
