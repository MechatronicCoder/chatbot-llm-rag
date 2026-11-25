"""Esquema del libro, validación del catálogo y de la tienda, e ISBN."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.catalogo.carga import (
    ErrorCatalogo,
    advertencias,
    cargar_catalogo,
    cargar_tienda,
)
from app.catalogo.modelo import Libro
from app.core.isbn import isbn13_valido, isbns_en, normalizar_isbn
from tests.datos_prueba import ISBN_CUENTOS, ISBN_MAGO, ISBN_SUMAS, LIBROS_PRUEBA


def libro(**cambios: Any) -> dict[str, Any]:
    base = {"isbn": ISBN_SUMAS, "titulo": "T", "autor": "A", "categoria": "Novela",
            "precio": 10, "stock": 1}  # fmt: skip
    return {**base, **cambios}


def escribir(tmp_path: Path, contenido: Any, nombre: str = "catalogo.json") -> Path:
    ruta = tmp_path / nombre
    texto = contenido if isinstance(contenido, str) else json.dumps(contenido, ensure_ascii=False)
    ruta.write_text(texto, encoding="utf-8")
    return ruta


# -- ISBN --------------------------------------------------------------------
def test_isbn13() -> None:
    assert isbn13_valido(ISBN_SUMAS)
    assert not isbn13_valido(ISBN_SUMAS[:-1] + str((int(ISBN_SUMAS[-1]) + 1) % 10))
    assert not isbn13_valido("1234567890123")  # sin prefijo 978/979
    assert not isbn13_valido("97800000000")  # corto
    assert normalizar_isbn("978-000-000-001-0 ") == "9780000000010"


def test_isbns_en_un_texto() -> None:
    texto = f"quiero el {ISBN_SUMAS[:3]}-{ISBN_SUMAS[3:]} y el {ISBN_MAGO}, no el 9780000000000"
    assert isbns_en(texto) == [ISBN_SUMAS, ISBN_MAGO]
    assert isbns_en("") == []


# -- un libro ------------------------------------------------------------------
def test_normaliza_campos() -> None:
    lb = Libro.model_validate(
        libro(
            isbn=f" {ISBN_SUMAS[:3]}-{ISBN_SUMAS[3:]} ",
            categoria="Novela",
            temas=["Magia", " magia ", "", "Escuela"],
            precio=10.555,
        )
    )
    assert lb.isbn == ISBN_SUMAS
    assert lb.categoria == "novela"
    assert lb.temas == ["magia", "escuela"]
    assert lb.precio == 10.56
    assert lb.portada is None and Libro.model_validate(libro(portada="")).portada is None


@pytest.mark.parametrize(
    ("cambios", "mensaje"),
    [
        ({"isbn": "123"}, "ISBN-13 inválido"),
        ({"edad_min": 10, "edad_max": 6}, "edad_min"),
        ({"valoracion": 4.5}, "num_valoraciones mayor que 0"),
        ({"num_valoraciones": 3}, "falta valoracion"),
        ({"similares": [ISBN_SUMAS]}, "similar a sí mismo"),
        ({"similares": ["111"]}, "ISBN-13 inválido"),
        ({"portada": "http://x.com/a.jpg"}, "ruta relativa"),
        ({"portada": "https://x.com/a b.jpg"}, "enlace https"),
        ({"portada": "../secreto.png"}, "ruta relativa"),
        ({"precio": 0}, "greater than 0"),
        ({"stock": -1}, "greater than or equal"),
        ({"campo_raro": 1}, "Extra inputs"),
    ],
)
def test_rechaza_datos_incoherentes(cambios: dict[str, Any], mensaje: str) -> None:
    with pytest.raises(ValidationError, match=mensaje):
        Libro.model_validate(libro(**cambios))


def test_texto_para_busqueda_y_fila() -> None:
    lb = Libro.model_validate(LIBROS_PRUEBA[2])
    texto = lb.texto_para_busqueda()
    assert "La escuela de magos" in texto and "magia" in texto and "mago" in texto
    fila = lb.a_fila()
    assert json.loads(fila["temas"]) == lb.temas
    assert json.loads(fila["similares"]) == lb.similares


# -- archivo del catálogo ---------------------------------------------------------
def test_carga_el_catalogo_de_prueba(tmp_path: Path) -> None:
    libros = cargar_catalogo(escribir(tmp_path, {"nota": "x", "libros": LIBROS_PRUEBA}))
    assert len(libros) == len(LIBROS_PRUEBA)


def test_junta_todos_los_problemas(tmp_path: Path) -> None:
    datos = {
        "libros": [
            libro(),
            libro(),  # repetido
            libro(isbn=ISBN_CUENTOS, edad_min=9, edad_max=2),
            libro(isbn=ISBN_MAGO, similares=[ISBN_CUENTOS]),  # similar inexistente
            "no es un libro",
        ]
    }
    with pytest.raises(ErrorCatalogo) as error:
        cargar_catalogo(escribir(tmp_path, datos))
    problemas = error.value.problemas
    assert len(problemas) == 4
    assert any("repetido" in p for p in problemas)
    assert any(f"libro 3 (isbn {ISBN_CUENTOS})" in p and "edad_min" in p for p in problemas)
    assert any("similares no existe" in p for p in problemas)
    assert any(p.startswith("libro 5:") for p in problemas)
    assert "4 problema(s)" in str(error.value)


@pytest.mark.parametrize(
    ("contenido", "mensaje"),
    [
        ("{no es json", "JSON inválido"),
        ({"productos": []}, "lista 'libros'"),
        ({"libros": []}, "está vacía"),
    ],
)
def test_archivo_mal_formado(tmp_path: Path, contenido: Any, mensaje: str) -> None:
    with pytest.raises(ErrorCatalogo, match=mensaje):
        cargar_catalogo(escribir(tmp_path, contenido))


def test_archivo_inexistente(tmp_path: Path) -> None:
    with pytest.raises(ErrorCatalogo, match="no existe"):
        cargar_catalogo(tmp_path / "nada.json")


def test_advertencias_de_datos_opcionales(tmp_path: Path) -> None:
    completo = {**LIBROS_PRUEBA[2], "similares": []}
    libros = cargar_catalogo(escribir(tmp_path, {"libros": [libro(), completo]}))
    avisos = advertencias(libros)
    assert len(avisos) == 1
    assert "sin sinopsis, temas, edad_min, valoracion" in avisos[0]


# -- tienda ------------------------------------------------------------------------
def test_tienda(tmp_path: Path) -> None:
    envio = {"lima": {"nombre": "Lima", "costo": 10, "gratis_desde": 120}}
    datos = {"nombre": "Libros X", "moneda": {"simbolo": "S/", "codigo": "PEN"}, "envio": envio}
    tienda = cargar_tienda(escribir(tmp_path, datos, "tienda.json"))
    assert tienda.precio(1234.5) == "S/ 1,234.50"
    assert tienda.envio["lima"].gratis_desde == 120
    with pytest.raises(ErrorCatalogo, match=r"tienda\.json"):
        cargar_tienda(escribir(tmp_path, {"nombre": ""}, "tienda.json"))
    sin_envio = {**datos, "envio": {}}
    with pytest.raises(ErrorCatalogo, match="envio"):
        cargar_tienda(escribir(tmp_path, sin_envio, "tienda.json"))


def test_portada_relativa_valida() -> None:
    lb = Libro.model_validate(libro(portada="portadas/9780000000019.webp"))
    assert lb.portada == "portadas/9780000000019.webp"


def test_portada_con_enlace_https() -> None:
    enlace = "https://m.media-amazon.com/images/S/compressed.photo.goodreads.com/books/1i/2.jpg"
    assert Libro.model_validate(libro(portada=enlace)).portada == enlace
