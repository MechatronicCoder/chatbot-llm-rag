"""Consultas del catálogo de libros: búsqueda por palabras, fichas y categorías.

Solo se ofrecen libros activos (los que siguen en `datos/catalogo.json`). La
búsqueda tolera errores de tipeo: una palabra que no está en el catálogo se
cambia por la más parecida si se le parece lo suficiente ("silmarilion" ->
"silmarillion", "marques" -> "marquez").
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from difflib import get_close_matches
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


# Parecido mínimo (difflib) para cambiar una palabra mal escrita por una del catálogo.
# Con 0.68, probado sobre el catálogo real, "cocina" pasaba a "conan".
UMBRAL_TIPEO = 0.75

# Orden de los resultados que coinciden igual con la consulta. "Los mejor valorados" o
# "los más baratos" salen de aquí: el modelo no puede ordenar el catálogo de memoria.
ORDENES: dict[str, Callable[[dict[str, Any]], tuple[float, ...]]] = {
    "valoracion": lambda lb: (-(lb["valoracion"] or 0.0), -(lb["num_valoraciones"] or 0)),
    "precio_asc": lambda lb: (lb["precio"],),
    "precio_desc": lambda lb: (-lb["precio"],),
}


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


def corregir(palabras: list[str], vocabulario: set[str]) -> tuple[list[str], dict[str, str]]:
    """Cambia cada palabra de 4 letras o más que no está en el catálogo por la más parecida.

    Solo entre las que empiezan con la misma letra y difieren en una letra de largo
    como máximo, como en un error de tipeo: así "drakula" pasa a "dracula", pero
    "receta" no pasa a "secreta" ni "cocina" a "conciencia". Si después de corregir
    queda alguna palabra que el catálogo no tiene ("harry poter"), no se corrige nada:
    el cliente pide algo que no hay y corregir solo una parte lo cambiaría por otra cosa.
    """
    corregidas, cambios = [], {}
    for palabra in palabras:
        cercana = None
        if palabra not in vocabulario and len(palabra) >= 4:
            candidatas = [
                v for v in vocabulario if v[0] == palabra[0] and abs(len(v) - len(palabra)) <= 1
            ]
            cercana = next(iter(get_close_matches(palabra, candidatas, 1, UMBRAL_TIPEO)), None)
        if cercana:
            cambios[palabra] = cercana
        corregidas.append(cercana or palabra)
    if any(p not in vocabulario for p in corregidas):
        return palabras, {}
    return corregidas, cambios


def en_presupuesto(libro: dict[str, Any], minimo: float | None, maximo: float | None) -> bool:
    return (minimo is None or libro["precio"] >= minimo) and (
        maximo is None or libro["precio"] <= maximo
    )


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
        precio_min: float | None = None,
        precio_max: float | None = None,
    ) -> list[dict[str, Any]]:
        """Los libros que encuentra `buscar` (ver ahí), sin el detalle de las correcciones."""
        return self.buscar(consulta, categoria, autor, edad, limite, precio_min, precio_max)[0]

    def buscar(
        self,
        consulta: str = "",
        categoria: str | None = None,
        autor: str | None = None,
        edad: int | None = None,
        limite: int = 5,
        precio_min: float | None = None,
        precio_max: float | None = None,
        orden: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Busca por palabras en título, autor, editorial, género, temas e ISBN.

        Un ISBN válido en la consulta devuelve ese libro directamente. Si no, ordena
        por cantidad de palabras completas que coinciden ("magos" encuentra "mago",
        pero "ica" no encuentra "matemáticas") y, a igualdad, por `orden` (valoración si
        no se indica, o precio). Sin consulta, lista el catálogo filtrado en ese orden.
        Devuelve también las correcciones de tipeo que aplicó (escrita -> del catálogo).
        """
        limite = max(1, min(int(limite), 20))
        if isbns := isbns_en(consulta):
            exactos = [self.obtener_libro(isbn) for isbn in isbns]
            return [lb for lb in exactos if lb is not None][:limite], {}
        todos = self.libros_activos()
        libros = [lb for lb in todos if en_presupuesto(lb, precio_min, precio_max)]
        if categoria:
            libros = [lb for lb in libros if lb["categoria"] == normalizar(categoria)]
        cambios: dict[str, str] = {}
        if autor:
            buscado = normalizar(autor)
            del_autor = [lb for lb in libros if buscado in normalizar(lb["autor"])]
            autores = {normalizar(lb["autor"]) for lb in libros} - {""}
            autores = {a for a in autores if a[0] == buscado[:1]}
            if not del_autor and (cercano := get_close_matches(buscado, autores, 1, UMBRAL_TIPEO)):
                cambios[buscado] = cercano[0]
                del_autor = [lb for lb in libros if normalizar(lb["autor"]) == cercano[0]]
            libros = del_autor
        if edad is not None:
            libros = [lb for lb in libros if en_rango_de_edad(lb, int(edad))]

        escritas = [
            raiz(p) for p in normalizar(consulta or "").split()
            if len(p) > 2 and p not in PALABRAS_VACIAS and not p.isdigit()
        ]  # fmt: skip
        vocabulario = set().union(*(_palabras_buscables(lb) for lb in todos))
        palabras, corregidas = corregir(escritas, vocabulario)
        cambios.update(corregidas)
        puntuados = []
        for libro in libros:
            disponibles = _palabras_buscables(libro)
            coincidencias = sum(1 for p in palabras if p in disponibles)
            if palabras and not coincidencias:
                continue
            puntuados.append((coincidencias, libro["valoracion"] or 0.0, libro))
        if corregidas:
            # Con palabras corregidas solo vale un libro que las tenga todas: si "garcia
            # marques" pasa a "grecia" y "marcu", ningún libro tiene las dos y no se inventa.
            puntuados = [t for t in puntuados if t[0] == len(palabras)]
            if not puntuados:
                cambios = {k: v for k, v in cambios.items() if k not in corregidas}
        # Un agotado solo aparece si es justo el libro pedido (tiene todas las palabras), para
        # decir que está agotado; como opción entre otras no sirve, porque no se puede comprar.
        puntuados = [
            t for t in puntuados if t[2]["stock"] > 0 or (palabras and t[0] == len(palabras))
        ]
        clave = ORDENES.get(orden or "", ORDENES["valoracion"])
        puntuados.sort(key=lambda t: (-t[0], *clave(t[2])))
        return [libro for *_, libro in puntuados[:limite]], cambios
