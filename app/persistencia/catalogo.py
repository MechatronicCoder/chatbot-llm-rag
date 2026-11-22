"""Consultas del catálogo de libros: búsqueda por palabras, fichas y categorías.

Solo se ofrecen libros activos (los que siguen en `datos/catalogo.json`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.core.isbn import isbns_en
from app.core.texto import normalizar
from app.persistencia.conexion import Conexion
from app.persistencia.esquema import fila_a_libro, raiz

# Palabras que aparecen en casi cualquier pedido y no ayudan a encontrar un libro.
PALABRAS_VACIAS = frozenset(
    (
        "los", "las", "del", "una", "uno", "unos", "unas", "que", "para", "con", "por",
        "sus", "libro", "libros", "quiero", "busco", "tienen", "tienes", "hay", "algun",
        "alguna", "algo", "sobre", "como",
    )
)  # fmt: skip


def en_rango_de_edad(libro: dict[str, Any], edad: int) -> bool:
    """Sin edad mínima se entiende 0; sin máxima, sin límite."""
    minimo = libro["edad_min"] if libro["edad_min"] is not None else 0
    maximo = libro["edad_max"] if libro["edad_max"] is not None else 99
    return minimo <= edad <= maximo


def _palabras_buscables(libro: dict[str, Any]) -> set[str]:
    """Palabras completas (sin plural) de los campos donde se busca."""
    campos = (
        libro["titulo"], libro["autor"], libro["editorial"], libro["categoria"],
        libro["genero"], libro["protagonista"], libro["isbn"], " ".join(libro["temas"]),
    )  # fmt: skip
    return {raiz(p) for p in normalizar(" ".join(campos)).split()}


class RepositorioCatalogo(Conexion):
    """Lectura del catálogo; la escritura del stock vive en los pedidos."""

    def libros_activos(self) -> list[dict[str, Any]]:
        with self.conectar() as con:
            filas = con.execute("select * from libros where activo = 1 order by isbn").fetchall()
        return [fila_a_libro(f) for f in filas]

    def obtener_libro(self, isbn: str) -> dict[str, Any] | None:
        """Libro activo por ISBN (acepta guiones y espacios)."""
        limpio = "".join(c for c in str(isbn) if c.isalnum()).upper()
        with self.conectar() as con:
            fila = con.execute(
                "select * from libros where isbn = ? and activo = 1", (limpio,)
            ).fetchone()
        return fila_a_libro(fila) if fila else None

    def libros_por_isbn(self, isbns: Iterable[str]) -> dict[str, dict[str, Any]]:
        buscados = set(isbns)
        return {
            libro["isbn"]: libro for libro in self.libros_activos() if libro["isbn"] in buscados
        }

    def categorias(self) -> list[dict[str, Any]]:
        with self.conectar() as con:
            filas = con.execute(
                "select categoria, count(*) as libros from libros where activo = 1 "
                "group by categoria order by libros desc, categoria"
            ).fetchall()
        return [dict(f) for f in filas]

    def buscar_libros(
        self,
        consulta: str = "",
        categoria: str | None = None,
        autor: str | None = None,
        edad: int | None = None,
        limite: int = 5,
    ) -> list[dict[str, Any]]:
        """Busca por palabras en título, autor, editorial, género, temas e ISBN.

        Un ISBN válido en la consulta devuelve ese libro directamente. Si no, ordena
        por cantidad de palabras completas que coinciden ("magos" encuentra "mago",
        pero "ica" no encuentra "matemáticas") y, a igualdad, por valoración. Sin
        consulta, lista el catálogo filtrado (lo mejor valorado primero).
        """
        limite = max(1, min(int(limite), 20))
        if isbns := isbns_en(consulta):
            exactos = [self.obtener_libro(isbn) for isbn in isbns]
            return [lb for lb in exactos if lb is not None][:limite]
        libros = self.libros_activos()
        if categoria:
            libros = [lb for lb in libros if lb["categoria"] == normalizar(categoria)]
        if autor:
            buscado = normalizar(autor)
            libros = [lb for lb in libros if buscado in normalizar(lb["autor"])]
        if edad is not None:
            libros = [lb for lb in libros if en_rango_de_edad(lb, int(edad))]

        palabras = [
            raiz(p) for p in normalizar(consulta or "").split()
            if len(p) > 2 and p not in PALABRAS_VACIAS and not p.isdigit()
        ]  # fmt: skip
        puntuados = []
        for libro in libros:
            disponibles = _palabras_buscables(libro)
            coincidencias = sum(1 for p in palabras if p in disponibles)
            if palabras and not coincidencias:
                continue
            puntuados.append((coincidencias, libro["valoracion"] or 0.0, libro))
        puntuados.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [libro for *_, libro in puntuados[:limite]]
