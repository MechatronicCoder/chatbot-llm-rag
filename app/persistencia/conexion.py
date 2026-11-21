"""Conexión a SQLite, creación del esquema y carga inicial del catálogo."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.persistencia.esquema import ESQUEMA


class Conexion:
    """Una conexión por operación: simple y seguro entre hilos."""

    def __init__(self, ruta: Path, ruta_catalogo: Path) -> None:
        self.ruta = ruta
        self.ruta_catalogo = ruta_catalogo

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

    # -- esquema y semilla -----------------------------------------------------
    def inicializar(self, recargar_catalogo: bool = False) -> dict[str, int]:
        """Crea el esquema y carga el catálogo si la tabla está vacía."""
        catalogo = json.loads(self.ruta_catalogo.read_text(encoding="utf-8"))
        with self.conectar() as con:
            con.executescript(ESQUEMA)
            con.execute("begin")
            try:
                hay = con.execute("select count(*) from productos").fetchone()[0]
                if recargar_catalogo:
                    con.execute("delete from productos")
                    hay = 0
                if not hay:
                    con.executemany(
                        "insert into productos (codigo, nombre, categoria, precio, specs, stock) "
                        "values (:codigo, :nombre, :categoria, :precio, :specs, :stock)",
                        catalogo["productos"],
                    )
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
        return self.estadisticas()

    def estadisticas(self) -> dict[str, int]:
        with self.conectar() as con:

            def contar(sql: str) -> int:
                return int(con.execute(sql).fetchone()[0])

            return {
                "productos": contar("select count(*) from productos"),
                "usuarios": contar("select count(*) from usuarios"),
                "pedidos": contar("select count(*) from pedidos"),
                "unidades": contar("select coalesce(sum(stock), 0) from productos"),
                "pagos": contar("select count(*) from pagos"),
                "mensajes": contar("select count(*) from mensajes"),
            }
