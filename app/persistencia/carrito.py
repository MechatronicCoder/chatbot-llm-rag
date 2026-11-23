"""El carrito: un pedido en estado `carrito`, uno abierto por cliente.

El carrito no aparta stock; solo comprueba que alcance. Cada cambio vuelve a leer
el precio de todos sus libros y guarda la cotización, así el total que ve el
cliente es el que se cobra. Reservar descuenta el stock de todas las líneas a la
vez (`RepositorioTienda._reservar_en_transaccion`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.catalogo.cotizacion import a_centimos
from app.persistencia.esquema import ErrorPedido, momento_actual
from app.persistencia.tienda import RepositorioTienda, nuevo_pedido_id

MAX_POR_LIBRO = 10
MAX_LINEAS = 20


class RepositorioCarrito(RepositorioTienda):
    """Armar, cotizar y reservar el carrito del cliente."""

    def _validar_zona(self, zona: str | None) -> str | None:
        zona = (zona or "").strip().lower() or None
        if zona is not None and zona not in self.tienda.envio:
            opciones = ", ".join(self.tienda.envio)
            raise ErrorPedido(f"Zona de envío desconocida: {zona!r}. Opciones: {opciones}.")
        return zona

    def _cambios_validos(self, cambios: Iterable[dict[str, Any]]) -> list[tuple[str, str, int]]:
        """(isbn, título, cantidad) con el ISBN del catálogo; falla antes de tocar nada."""
        validos = []
        for cambio in cambios:
            cantidad = int(cambio.get("cantidad", 1))
            if not 0 <= cantidad <= MAX_POR_LIBRO:
                raise ErrorPedido(f"La cantidad por libro va de 0 a {MAX_POR_LIBRO}.")
            libro = self.obtener_libro(str(cambio.get("isbn", "")))
            if libro is None:
                raise ErrorPedido(
                    f"No hay un libro con ISBN {cambio.get('isbn')!r} en el catálogo."
                )
            if cantidad > libro["stock"]:
                raise ErrorPedido(
                    f"Solo quedan {libro['stock']} ejemplares de «{libro['titulo']}»."
                )
            validos.append((libro["isbn"], libro["titulo"], cantidad))
        return validos

    def carrito_de(self, usuario_id: str) -> dict[str, Any] | None:
        with self.conectar() as con:
            fila = con.execute(
                "select * from pedidos where usuario_id = ? and estado = 'carrito'", (usuario_id,)
            ).fetchone()
            return self._armar(con, fila) if fila else None

    def actualizar_carrito(
        self, usuario_id: str, cambios: Iterable[dict[str, Any]] = (), zona: str | None = None
    ) -> dict[str, Any]:
        """Fija la cantidad de cada libro (0 lo quita) y la zona; devuelve el carrito cotizado."""
        zona = self._validar_zona(zona)
        validos = self._cambios_validos(cambios)
        self.asegurar_usuario(usuario_id)
        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                fila = con.execute(
                    "select id from pedidos where usuario_id = ? and estado = 'carrito'",
                    (usuario_id,),
                ).fetchone()
                pedido_id = fila["id"] if fila else nuevo_pedido_id()
                if fila is None:
                    ahora = momento_actual()
                    con.execute(
                        "insert into pedidos (id, usuario_id, estado, creado_en, actualizado_en) "
                        "values (?, ?, 'carrito', ?, ?)",
                        (pedido_id, usuario_id, ahora, ahora),
                    )
                for isbn, _, cantidad in validos:
                    if cantidad == 0:
                        con.execute(
                            "delete from pedido_lineas where pedido_id = ? and isbn = ?",
                            (pedido_id, isbn),
                        )
                        continue
                    con.execute(
                        "insert into pedido_lineas (pedido_id, isbn, cantidad, precio_cent, orden) "
                        "values (?, ?, ?, 0, (select coalesce(max(orden), 0) + 1 from "
                        "pedido_lineas where pedido_id = ?)) "
                        "on conflict(pedido_id, isbn) do update set cantidad = excluded.cantidad",
                        (pedido_id, isbn, cantidad, pedido_id),
                    )
                lineas = con.execute(
                    "select pl.isbn, l.precio from pedido_lineas pl join libros l "
                    "on l.isbn = pl.isbn where pl.pedido_id = ?",
                    (pedido_id,),
                ).fetchall()
                if len(lineas) > MAX_LINEAS:
                    raise ErrorPedido(f"El carrito admite hasta {MAX_LINEAS} libros distintos.")
                for linea in lineas:  # el precio vigente de todos los libros, no solo los nuevos
                    con.execute(
                        "update pedido_lineas set precio_cent = ? where pedido_id = ? and isbn = ?",
                        (a_centimos(linea["precio"]), pedido_id, linea["isbn"]),
                    )
                if zona is not None:
                    con.execute("update pedidos set zona = ? where id = ?", (zona, pedido_id))
                self._guardar_cotizacion(con, pedido_id)
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
            return self._armar(con, self._pedido_en_transaccion(con, pedido_id))

    def reservar_carrito(self, usuario_id: str) -> dict[str, Any]:
        """Reserva el carrito abierto: descuenta stock y fija los montos."""
        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                fila = con.execute(
                    "select id from pedidos where usuario_id = ? and estado = 'carrito'",
                    (usuario_id,),
                ).fetchone()
                if fila is None:
                    raise ErrorPedido(
                        "No hay un carrito abierto: agrega libros con actualizar_carrito."
                    )
                cambios = self._reservar_en_transaccion(con, fila["id"])
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
            pedido = self._armar(con, self._pedido_en_transaccion(con, fila["id"]))
        return {**pedido, "cambios_de_precio": cambios} if cambios else pedido

    def reservar_pedido(
        self, usuario_id: str, isbn: str, cantidad: int, zona: str = "lima"
    ) -> dict[str, Any]:
        """Atajo de un solo libro que no toca el carrito abierto (demo y pruebas)."""
        (isbn_valido, _, cantidad), *_ = self._cambios_validos(
            [{"isbn": isbn, "cantidad": cantidad}]
        )
        if cantidad == 0:
            raise ErrorPedido("La cantidad debe ser mayor que cero.")
        zona_valida = self._validar_zona(zona)
        self.asegurar_usuario(usuario_id)
        pedido_id = nuevo_pedido_id()
        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                ahora = momento_actual()
                con.execute(
                    "insert into pedidos (id, usuario_id, estado, zona, creado_en, actualizado_en) "
                    "values (?, ?, 'carrito', ?, ?, ?)",
                    (pedido_id, usuario_id, zona_valida, ahora, ahora),
                )
                precio = con.execute("select precio from libros where isbn = ?", (isbn_valido,))
                con.execute(
                    "insert into pedido_lineas (pedido_id, isbn, cantidad, precio_cent, orden) "
                    "values (?, ?, ?, ?, 1)",
                    (pedido_id, isbn_valido, cantidad, a_centimos(precio.fetchone()["precio"])),
                )
                self._reservar_en_transaccion(con, pedido_id)
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
            return self._armar(con, self._pedido_en_transaccion(con, pedido_id))
