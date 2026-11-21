"""Catálogo, usuarios, pedidos y pagos.

La reserva descuenta stock y crea el pedido en la misma transacción, así dos
reservas simultáneas no venden lo mismo dos veces; un pedido no se cobra dos
veces ni se cobra el de otro cliente.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from typing import Any

from app.persistencia.conexion import Conexion
from app.persistencia.esquema import (
    METODOS_PAGO,
    ErrorPedido,
    fila_a_pedido,
    fila_a_producto,
    momento_actual,
    raiz,
)


class RepositorioTienda(Conexion):
    """Operaciones de la tienda sobre la base."""

    # -- productos ---------------------------------------------------------------
    def buscar_productos(
        self, consulta: str = "", categoria: str | None = None, limite: int = 5
    ) -> list[dict[str, Any]]:
        """Busca por palabras sueltas: 'laptops disponibles' encuentra las laptops."""
        limite = max(1, min(int(limite), 20))
        with self.conectar() as con:
            filas = [fila_a_producto(f) for f in con.execute("select * from productos").fetchall()]

        if categoria:
            filas = [f for f in filas if f["categoria"].lower() == categoria.lower()]

        palabras = [raiz(p) for p in re.split(r"\W+", (consulta or "").lower()) if len(p) > 2]
        if palabras:

            def coincide(producto: dict[str, Any]) -> bool:
                texto = raiz(
                    f"{producto['nombre']} {producto['categoria']} "
                    f"{producto['codigo']} {producto['specs']}".lower()
                )
                return any(p in texto for p in palabras)

            filas = [f for f in filas if coincide(f)]

        return sorted(filas, key=lambda f: f["precio"])[:limite]

    def obtener_producto(self, codigo: str) -> dict[str, Any] | None:
        with self.conectar() as con:
            fila = con.execute(
                "select * from productos where upper(codigo) = upper(?)", (codigo,)
            ).fetchone()
        return fila_a_producto(fila) if fila else None

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

    # -- pedidos -----------------------------------------------------------------
    def reservar_pedido(self, usuario_id: str, codigo: str, cantidad: int) -> dict[str, Any]:
        """Crea el pedido y descuenta el stock en una sola transacción."""
        cantidad = int(cantidad)
        if cantidad <= 0:
            raise ErrorPedido("La cantidad debe ser mayor que cero.")
        producto = self.obtener_producto(codigo)
        if producto is None:
            raise ErrorPedido(f"No existe el producto {codigo!r}. Usa buscar_productos.")
        self.asegurar_usuario(usuario_id)

        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                cursor = con.execute(
                    "update productos set stock = stock - ? "
                    "where upper(codigo) = upper(?) and stock >= ?",
                    (cantidad, codigo, cantidad),
                )
                if cursor.rowcount == 0:
                    disponible = con.execute(
                        "select stock from productos where upper(codigo) = upper(?)", (codigo,)
                    ).fetchone()["stock"]
                    raise ErrorPedido(
                        f"Solo quedan {disponible} unidades de {producto['nombre']} "
                        f"(se pidieron {cantidad})."
                    )
                pedido_id = f"PED-{uuid.uuid4().hex[:8].upper()}"
                total = round(producto["precio"] * cantidad, 2)
                con.execute(
                    "insert into pedidos (id, usuario_id, codigo, cantidad, precio_unitario, "
                    "total, estado, creado_en) values (?, ?, ?, ?, ?, ?, 'reservado', ?)",
                    (
                        pedido_id,
                        usuario_id,
                        producto["codigo"],
                        cantidad,
                        producto["precio"],
                        total,
                        momento_actual(),
                    ),
                )
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise

        return {
            "pedido_id": pedido_id,
            "estado": "reservado",
            "usuario_id": usuario_id,
            "codigo": producto["codigo"],
            "nombre": producto["nombre"],
            "cantidad": cantidad,
            "precio_unitario": producto["precio"],
            "total": total,
            "stock_restante": producto["stock"] - cantidad,
        }

    def consultar_pedido(self, pedido_id: str) -> dict[str, Any] | None:
        with self.conectar() as con:
            fila = con.execute(
                "select p.*, pr.nombre from pedidos p join productos pr on pr.codigo = p.codigo "
                "where upper(p.id) = upper(?)",
                (pedido_id,),
            ).fetchone()
        return fila_a_pedido(fila) if fila else None

    def pedidos_de(self, usuario_id: str) -> list[dict[str, Any]]:
        with self.conectar() as con:
            filas = con.execute(
                "select p.*, pr.nombre from pedidos p join productos pr on pr.codigo = p.codigo "
                "where p.usuario_id = ? order by p.creado_en desc, p.rowid desc",
                (usuario_id,),
            ).fetchall()
        return [fila_a_pedido(f) for f in filas]

    def _pedido_en_transaccion(self, con: sqlite3.Connection, pedido_id: str) -> sqlite3.Row:
        fila = con.execute(
            "select * from pedidos where upper(id) = upper(?)", (pedido_id,)
        ).fetchone()
        if fila is None:
            raise ErrorPedido(f"No existe el pedido {pedido_id!r}.")
        return fila

    def cancelar_pedido(self, pedido_id: str) -> dict[str, Any]:
        """Cancela un pedido reservado y devuelve las unidades al stock."""
        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                fila = self._pedido_en_transaccion(con, pedido_id)
                if fila["estado"] != "reservado":
                    raise ErrorPedido(
                        f"El pedido {fila['id']} está en estado {fila['estado']!r}: "
                        "no se puede cancelar."
                    )
                con.execute(
                    "update productos set stock = stock + ? where codigo = ?",
                    (fila["cantidad"], fila["codigo"]),
                )
                con.execute("update pedidos set estado = 'cancelado' where id = ?", (fila["id"],))
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
        return {
            "pedido_id": fila["id"],
            "estado": "cancelado",
            "unidades_devueltas": fila["cantidad"],
        }

    # -- pagos -------------------------------------------------------------------
    def registrar_pago(
        self, usuario_id: str, pedido_id: str, metodo: str = "tarjeta"
    ) -> dict[str, Any]:
        """Cobra un pedido reservado del mismo cliente; nunca dos veces."""
        metodo = (metodo or "tarjeta").strip().lower()
        if metodo not in METODOS_PAGO:
            raise ErrorPedido(f"Método no aceptado. Opciones: {', '.join(METODOS_PAGO)}.")

        with self.conectar() as con:
            con.execute("begin immediate")
            try:
                fila = self._pedido_en_transaccion(con, pedido_id)
                if fila["usuario_id"] != usuario_id:
                    raise ErrorPedido("Ese pedido pertenece a otro cliente.")
                if fila["estado"] != "reservado":
                    raise ErrorPedido(
                        f"El pedido {fila['id']} está en estado {fila['estado']!r}: "
                        "no se puede cobrar."
                    )
                pago_id = f"PAG-{uuid.uuid4().hex[:8].upper()}"
                con.execute(
                    "insert into pagos (id, pedido_id, metodo, monto, estado, creado_en) "
                    "values (?, ?, ?, ?, 'aprobado', ?)",
                    (pago_id, fila["id"], metodo, fila["total"], momento_actual()),
                )
                con.execute("update pedidos set estado = 'pagado' where id = ?", (fila["id"],))
                con.execute("commit")
            except Exception:
                con.execute("rollback")
                raise
        return {
            "pago_id": pago_id,
            "pedido_id": fila["id"],
            "metodo": metodo,
            "monto": fila["total"],
            "estado": "pagado",
        }

    def pagos_de(self, pedido_id: str) -> list[dict[str, Any]]:
        with self.conectar() as con:
            filas = con.execute(
                "select * from pagos where pedido_id = ? order by creado_en", (pedido_id,)
            ).fetchall()
        return [dict(f) for f in filas]

    def ventas(self, usuario_id: str | None = None) -> list[dict[str, Any]]:
        """Pedidos pagados (reporte y CSV)."""
        consulta = (
            "select p.*, pr.nombre from pedidos p join productos pr on pr.codigo = p.codigo "
            "where p.estado = 'pagado' and (? is null or p.usuario_id = ?) "
            "order by p.creado_en, p.rowid"
        )
        with self.conectar() as con:
            filas = con.execute(consulta, (usuario_id, usuario_id)).fetchall()
        return [fila_a_pedido(f) for f in filas]
