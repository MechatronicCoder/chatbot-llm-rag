"""Conexión a SQLite, versión del esquema y sincronización del catálogo.

El archivo `datos/catalogo.json` es la fuente de verdad de los datos de cada
libro; la base es la fuente de verdad del stock mientras la app está en uso.
Al arrancar se sincronizan:

- un libro nuevo en el archivo se agrega con el stock del archivo;
- un libro existente actualiza sus datos y su precio, pero conserva el stock de
  la base, porque las reservas ya lo descontaron;
- un libro que desaparece del archivo se marca como inactivo (no se borra: puede
  tener pedidos).

`inicializar(recargar_catalogo=True)` además repone el stock del archivo.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path

from app.catalogo.carga import Tienda, cargar_catalogo, cargar_tienda
from app.catalogo.modelo import Libro
from app.persistencia.esquema import COLUMNAS_LIBRO, ESQUEMA, ESQUEMA_VERSION, TABLAS

# Columnas fijas del esquema; los valores siempre van parametrizados.
_INSERTAR = (
    f"insert into libros ({', '.join(COLUMNAS_LIBRO)}, activo) "  # noqa: S608
    f"values ({', '.join(':' + c for c in COLUMNAS_LIBRO)}, 1)"
)
_ACTUALIZAR_DATOS = ", ".join(f"{c} = :{c}" for c in COLUMNAS_LIBRO if c not in ("isbn", "stock"))


class Conexion:
    """Una conexión por operación: simple y seguro entre hilos."""

    def __init__(self, ruta: Path, ruta_catalogo: Path) -> None:
        self.ruta = ruta
        self.ruta_catalogo = ruta_catalogo
        self.ultima_sincronizacion: dict[str, int] = {}

    @cached_property
    def tienda(self) -> Tienda:
        """Moneda y tarifas de envío: `tienda.json`, junto al catálogo."""
        return cargar_tienda(self.ruta_catalogo.parent / "tienda.json")

    @contextmanager
    def conectar(self) -> Iterator[sqlite3.Connection]:
        """Conexión con filas tipo diccionario; las transacciones se abren a mano."""
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.ruta, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("pragma journal_mode=WAL")
            con.execute("pragma foreign_keys=on")
            yield con
        finally:
            con.close()

    # -- esquema y catálogo ----------------------------------------------------
    def inicializar(self, recargar_catalogo: bool = False) -> dict[str, int]:
        """Valida el catálogo, prepara el esquema y sincroniza los libros."""
        libros = cargar_catalogo(self.ruta_catalogo)  # si es inválido, no se toca la base
        with self.conectar() as con:
            if con.execute("pragma user_version").fetchone()[0] != ESQUEMA_VERSION:
                con.execute("pragma foreign_keys=off")
                for tabla in TABLAS:
                    con.execute(f"drop table if exists {tabla}")
                con.execute("pragma foreign_keys=on")
            con.executescript(ESQUEMA)
            con.execute(f"pragma user_version = {ESQUEMA_VERSION}")
            con.execute("begin")
            try:
                self.ultima_sincronizacion = self._sincronizar(con, libros, recargar_catalogo)
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
        return self.estadisticas()

    @staticmethod
    def _sincronizar(
        con: sqlite3.Connection, libros: list[Libro], reponer_stock: bool
    ) -> dict[str, int]:
        existentes = {fila[0] for fila in con.execute("select isbn from libros")}
        nuevos = actualizados = 0
        asignar_stock = ", stock = :stock" if reponer_stock else ""
        for libro in libros:
            fila = libro.a_fila()
            if libro.isbn in existentes:
                con.execute(
                    f"update libros set {_ACTUALIZAR_DATOS}{asignar_stock}, activo = 1 "  # noqa: S608 - columnas fijas
                    "where isbn = :isbn",
                    fila,
                )
                actualizados += 1
            else:
                con.execute(_INSERTAR, fila)
                nuevos += 1
        en_archivo = {libro.isbn for libro in libros}
        retirados = sorted(existentes - en_archivo)
        con.executemany(
            "update libros set activo = 0 where isbn = ?", [(isbn,) for isbn in retirados]
        )
        return {"nuevos": nuevos, "actualizados": actualizados, "retirados": len(retirados)}

    def estadisticas(self) -> dict[str, int]:
        with self.conectar() as con:

            def contar(sql: str) -> int:
                return int(con.execute(sql).fetchone()[0])

            return {
                "libros": contar("select count(*) from libros where activo = 1"),
                "unidades": contar("select coalesce(sum(stock), 0) from libros where activo = 1"),
                "usuarios": contar("select count(*) from usuarios"),
                "pedidos": contar("select count(*) from pedidos"),
                "pagos": contar("select count(*) from pagos"),
                "mensajes": contar("select count(*) from mensajes"),
            }
