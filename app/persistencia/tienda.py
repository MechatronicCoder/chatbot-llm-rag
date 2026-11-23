"""Usuarios, pedidos y pagos.

Un pedido nace como carrito (`carrito.py`) y al reservarse descuenta el stock de
todas sus líneas en una sola transacción, así dos reservas simultáneas no venden
el último ejemplar dos veces. Un pedido no se cobra dos veces ni se cobra el de
otro cliente. Los montos se calculan en `app.catalogo.cotizacion`, en céntimos.
"""

from __future__ import annotations

import sqlite3
import uuid
from typing import Any

from app.catalogo.cotizacion import Cotizacion, Linea, a_centimos, a_soles, cotizar, detalle
from app.persistencia.catalogo import RepositorioCatalogo
from app.persistencia.esquema import METODOS_PAGO, ErrorPedido, momento_actual

_LINEAS = (
    "select pl.isbn, pl.cantidad, pl.precio_cent, l.titulo, l.autor, l.precio, l.stock, "
    "l.activo from pedido_lineas pl join libros l on l.isbn = pl.isbn "
    "where pl.pedido_id = ? order by pl.orden"
)


def nuevo_pedido_id() -> str:
    return f"PED-{uuid.uuid4().hex[:8].upper()}"


class RepositorioTienda(RepositorioCatalogo):
    """Operaciones de venta sobre el catálogo."""

    # -- usuarios ----------------------------------------------------------------
    def asegurar_usuario(self, usuario_id: str, nombre: str = "") -> dict[str, Any]:
        """Crea el usuario si no existe y lo devuelve."""
        usuario_id = (usuario_id or "anonimo").strip()
        with self.conectar() as con:
            con.execute(
                "insert into usuarios (id, nombre, email, creado_en) values (?, ?, ?, ?) "
                "on conflict(id) do nothing",
                (usuario_id, nombre or usuario_id, None, momento_actual()),
            )
            fila = con.execute("select * from usuarios where id = ?", (usuario_id,)).fetchone()
        return {"id": fila["id"], "nombre": fila["nombre"], "creado_en": fila["creado_en"]}

    # -- armado del pedido ---------------------------------------------------------
    def _cotizacion(self, con: sqlite3.Connection, fila: sqlite3.Row) -> Cotizacion:
        lineas = [
            Linea(f["isbn"], f["titulo"], f["autor"], f["cantidad"], f["precio_cent"])
            for f in con.execute(_LINEAS, (fila["id"],)).fetchall()
        ]
        if fila["estado"] == "carrito":
            return cotizar(lineas, fila["zona"], self.tienda)
        # reservado, pagado o cancelado: los montos quedaron fijos al reservar
        return Cotizacion(
            tuple(lineas), fila["zona"], fila["subtotal_cent"], fila["envio_cent"], None
        )

    def _armar(self, con: sqlite3.Connection, fila: sqlite3.Row) -> dict[str, Any]:
        return {
            "pedido_id": fila["id"],
            "usuario_id": fila["usuario_id"],
            "estado": fila["estado"],
            **detalle(self._cotizacion(con, fila), self.tienda),
            "creado_en": fila["creado_en"],
        }

    def _guardar_cotizacion(self, con: sqlite3.Connection, pedido_id: str) -> None:
        fila = self._pedido_en_transaccion(con, pedido_id)
        cotizacion = cotizar(self._cotizacion(con, fila).lineas, fila["zona"], self.tienda)
        con.execute(
            "update pedidos set subtotal_cent = ?, envio_cent = ?, total_cent = ?, "
            "actualizado_en = ? where id = ?",
            (
                cotizacion.subtotal_cent,
                cotizacion.envio_cent,
                cotizacion.total_cent,
                momento_actual(),
                pedido_id,
            ),
        )

    def _pedido_en_transaccion(self, con: sqlite3.Connection, pedido_id: str) -> sqlite3.Row:
        fila = con.execute(
            "select * from pedidos where upper(id) = upper(?)", (pedido_id,)
        ).fetchone()
        if fila is None:
            raise ErrorPedido(f"No existe el pedido {pedido_id!r}.")
        return fila

    def _reservar_en_transaccion(self, con: sqlite3.Connection, pedido_id: str) -> list[str]:
        """Descuenta el stock de cada línea con el precio actual; devuelve los cambios de precio."""
        fila = self._pedido_en_transaccion(con, pedido_id)
        lineas = con.execute(_LINEAS, (fila["id"],)).fetchall()
        if not lineas:
            raise ErrorPedido(
                "El carrito está vacío: primero agrega libros con actualizar_carrito."
            )
        if fila["zona"] not in self.tienda.envio:
            raise ErrorPedido(
                "Falta la zona de envío: pregunta si es Lima o provincias y pásala a "
                "actualizar_carrito antes de reservar."
            )
        cambios = []
        for linea in lineas:
            cursor = con.execute(
                "update libros set stock = stock - ? where isbn = ? and activo = 1 and stock >= ?",
                (linea["cantidad"], linea["isbn"], linea["cantidad"]),
            )
            if cursor.rowcount == 0:
                raise ErrorPedido(
                    f"Solo quedan {linea['stock'] if linea['activo'] else 0} ejemplares de "
                    f"«{linea['titulo']}» (se pidieron {linea['cantidad']})."
                )
            actual = a_centimos(linea["precio"])
            if actual != linea["precio_cent"]:
                precio = self.tienda.precio
                cambios.append(
                    f"«{linea['titulo']}» pasó de {precio(a_soles(linea['precio_cent']))} "
                    f"a {precio(a_soles(actual))}"
                )
                con.execute(
                    "update pedido_lineas set precio_cent = ? where pedido_id = ? and isbn = ?",
                    (actual, fila["id"], linea["isbn"]),
                )
        self._guardar_cotizacion(con, fila["id"])
        con.execute("update pedidos set estado = 'reservado' where id = ?", (fila["id"],))
        return cambios

    # -- consultas -------------------------------------------------------------------
    def consultar_pedido(self, pedido_id: str) -> dict[str, Any] | None:
        with self.conectar() as con:
            fila = con.execute(
                "select * from pedidos where upper(id) = upper(?)", (pedido_id,)
            ).fetchone()
            return self._armar(con, fila) if fila else None

    def pedidos_de(self, usuario_id: str) -> list[dict[str, Any]]:
        """Pedidos del cliente, del más nuevo al más viejo; un carrito vacío no cuenta."""
        with self.conectar() as con:
            filas = con.execute(
                "select * from pedidos where usuario_id = ? order by creado_en desc, rowid desc",
                (usuario_id,),
            ).fetchall()
            pedidos = [self._armar(con, f) for f in filas]
        return [p for p in pedidos if p["lineas"]]

    def ventas(self, usuario_id: str | None = None) -> list[dict[str, Any]]:
        """Una fila por libro vendido en pedidos pagados (reporte y CSV)."""
        with self.conectar() as con:
            filas = con.execute(
                "select * from pedidos where estado = 'pagado' and (? is null or usuario_id = ?) "
                "order by creado_en, rowid",
                (usuario_id, usuario_id),
            ).fetchall()
            pedidos = [self._armar(con, f) for f in filas]
        return [
            {
                "pedido_id": p["pedido_id"],
                "usuario_id": p["usuario_id"],
                **linea,
                "envio": p["envio"] or 0.0,
                "total": p["total"],
                "estado": p["estado"],
                "creado_en": p["creado_en"],
            }
            for p in pedidos
            for linea in p["lineas"]
        ]

    # -- cambios de estado -------------------------------------------------------------
    def cancelar_pedido(self, pedido_id: str) -> dict[str, Any]:
        """Cancela un carrito o un pedido reservado; lo reservado vuelve al stock."""
        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                fila = self._pedido_en_transaccion(con, pedido_id)
                if fila["estado"] not in ("carrito", "reservado"):
                    raise ErrorPedido(
                        f"El pedido {fila['id']} está en estado {fila['estado']!r}: "
                        "no se puede cancelar."
                    )
                devueltas = 0
                if fila["estado"] == "reservado":
                    for linea in con.execute(_LINEAS, (fila["id"],)).fetchall():
                        con.execute(
                            "update libros set stock = stock + ? where isbn = ?",
                            (linea["cantidad"], linea["isbn"]),
                        )
                        devueltas += linea["cantidad"]
                con.execute(
                    "update pedidos set estado = 'cancelado', actualizado_en = ? where id = ?",
                    (momento_actual(), fila["id"]),
                )
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
            pedido = self._armar(con, self._pedido_en_transaccion(con, fila["id"]))
        return {**pedido, "unidades_devueltas": devueltas}

    def registrar_pago(
        self, usuario_id: str, pedido_id: str, metodo: str = "tarjeta"
    ) -> dict[str, Any]:
        """Cobra un pedido del mismo cliente, nunca dos veces; un carrito se reserva y cobra."""
        metodo = (metodo or "tarjeta").strip().lower()
        if metodo not in METODOS_PAGO:
            raise ErrorPedido(f"Método no aceptado. Opciones: {', '.join(METODOS_PAGO)}.")

        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                fila = self._pedido_en_transaccion(con, pedido_id)
                if fila["usuario_id"] != usuario_id:
                    raise ErrorPedido("Ese pedido pertenece a otro cliente.")
                if fila["estado"] == "carrito":
                    cambios = self._reservar_en_transaccion(con, fila["id"])
                    if cambios:
                        raise ErrorPedido(
                            "No se cobró: cambió el precio de " + "; ".join(cambios) + ". "
                            "Muestra el total nuevo con actualizar_carrito y pide confirmación."
                        )
                    fila = self._pedido_en_transaccion(con, fila["id"])
                if fila["estado"] != "reservado":
                    raise ErrorPedido(
                        f"El pedido {fila['id']} está en estado {fila['estado']!r}: "
                        "no se puede cobrar."
                    )
                pago_id = f"PAG-{uuid.uuid4().hex[:8].upper()}"
                con.execute(
                    "insert into pagos (id, pedido_id, metodo, monto_cent, estado, creado_en) "
                    "values (?, ?, ?, ?, 'aprobado', ?)",
                    (pago_id, fila["id"], metodo, fila["total_cent"], momento_actual()),
                )
                con.execute(
                    "update pedidos set estado = 'pagado', actualizado_en = ? where id = ?",
                    (momento_actual(), fila["id"]),
                )
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
        return {
            "pago_id": pago_id,
            "pedido_id": fila["id"],
            "metodo": metodo,
            "monto": a_soles(fila["total_cent"]),
            "estado": "pagado",
        }

    def pagos_de(self, pedido_id: str) -> list[dict[str, Any]]:
        with self.conectar() as con:
            filas = con.execute(
                "select * from pagos where pedido_id = ? order by creado_en", (pedido_id,)
            ).fetchall()
        return [{**dict(f), "monto": a_soles(f["monto_cent"])} for f in filas]
