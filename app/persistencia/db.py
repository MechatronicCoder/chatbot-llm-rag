"""Base de datos SQLite: catálogo, usuarios, pedidos, pagos y conversación.

Reglas que se cumplen aquí y no en el modelo de lenguaje:

- la reserva descuenta stock y crea el pedido en la misma transacción, así dos
  reservas simultáneas no venden lo mismo dos veces;
- un pedido no se cobra dos veces ni se cobra el de otro cliente;
- los mensajes se guardan siempre redactados, nunca con el dato sensible original.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ESQUEMA = """
create table if not exists productos (
    codigo      text primary key,
    nombre      text not null,
    categoria   text not null,
    precio      real not null,
    specs       text not null,
    stock       integer not null
);
create table if not exists usuarios (
    id          text primary key,
    nombre      text,
    email       text,
    creado_en   text not null
);
create table if not exists pedidos (
    id              text primary key,
    usuario_id      text not null references usuarios(id),
    codigo          text not null references productos(codigo),
    cantidad        integer not null,
    precio_unitario real not null,
    total           real not null,
    estado          text not null,          -- reservado | pagado | cancelado
    creado_en       text not null
);
create index if not exists idx_pedidos_usuario on pedidos(usuario_id);
create table if not exists pagos (
    id          text primary key,
    pedido_id   text not null references pedidos(id),
    metodo      text not null,              -- tarjeta | efectivo | transferencia
    monto       real not null,
    estado      text not null,
    creado_en   text not null
);
create table if not exists mensajes (
    id                   text primary key,
    turno_id             text not null,
    sesion_id            text not null,
    usuario_id           text not null,
    rol                  text not null,     -- user | assistant
    contenido_redactado  text not null,
    ruta                 text,
    fuente               text,
    faq_id               text,
    similitud            real,
    tokens_entrada       integer default 0,
    tokens_salida        integer default 0,
    latencia_ms          integer default 0,
    herramientas_json    text,
    creado_en            text not null
);
create index if not exists idx_mensajes_sesion on mensajes(sesion_id);
create index if not exists idx_mensajes_turno on mensajes(turno_id);
"""

METODOS_PAGO = ("tarjeta", "efectivo", "transferencia")


class ErrorPedido(ValueError):
    """Operación rechazada: producto inexistente, sin stock o pedido en otro estado."""


def _ahora() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _raiz(palabra: str) -> str:
    """'laptops' -> 'laptop', 'monitores' -> 'monitor' (suficiente para buscar)."""
    for sufijo in ("es", "s"):
        if len(palabra) > 4 and palabra.endswith(sufijo):
            return palabra[: -len(sufijo)]
    return palabra


def _producto(fila: sqlite3.Row) -> dict[str, Any]:
    return {
        "codigo": fila["codigo"],
        "nombre": fila["nombre"],
        "categoria": fila["categoria"],
        "precio": fila["precio"],
        "specs": fila["specs"],
        "stock": fila["stock"],
    }


def _pedido(fila: sqlite3.Row) -> dict[str, Any]:
    return {
        "pedido_id": fila["id"],
        "usuario_id": fila["usuario_id"],
        "codigo": fila["codigo"],
        "nombre": fila["nombre"],
        "cantidad": fila["cantidad"],
        "precio_unitario": fila["precio_unitario"],
        "total": fila["total"],
        "estado": fila["estado"],
        "creado_en": fila["creado_en"],
    }


class BaseDatos:
    """Acceso a SQLite. Una conexión por operación: simple y seguro entre hilos."""

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

    # -- productos ---------------------------------------------------------------
    def buscar_productos(
        self, consulta: str = "", categoria: str | None = None, limite: int = 5
    ) -> list[dict[str, Any]]:
        """Busca por palabras sueltas: 'laptops disponibles' encuentra las laptops."""
        limite = max(1, min(int(limite), 20))
        with self.conectar() as con:
            filas = [_producto(f) for f in con.execute("select * from productos").fetchall()]

        if categoria:
            filas = [f for f in filas if f["categoria"].lower() == categoria.lower()]

        palabras = [_raiz(p) for p in re.split(r"\W+", (consulta or "").lower()) if len(p) > 2]
        if palabras:

            def coincide(producto: dict[str, Any]) -> bool:
                texto = _raiz(
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
        return _producto(fila) if fila else None

    # -- usuarios ----------------------------------------------------------------
    def asegurar_usuario(self, usuario_id: str, nombre: str = "") -> dict[str, Any]:
        """Crea el usuario si no existe y lo devuelve."""
        usuario_id = (usuario_id or "anonimo").strip()
        with self.conectar() as con:
            con.execute(
                "insert into usuarios (id, nombre, email, creado_en) values (?, ?, ?, ?) "
                "on conflict(id) do nothing",
                (usuario_id, nombre or usuario_id, None, _ahora()),
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
                        _ahora(),
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
        return _pedido(fila) if fila else None

    def pedidos_de(self, usuario_id: str) -> list[dict[str, Any]]:
        with self.conectar() as con:
            filas = con.execute(
                "select p.*, pr.nombre from pedidos p join productos pr on pr.codigo = p.codigo "
                "where p.usuario_id = ? order by p.creado_en desc, p.rowid desc",
                (usuario_id,),
            ).fetchall()
        return [_pedido(f) for f in filas]

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
                    (pago_id, fila["id"], metodo, fila["total"], _ahora()),
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
        return [_pedido(f) for f in filas]

    # -- conversación ------------------------------------------------------------
    def guardar_turno(
        self,
        turno_id: str,
        sesion_id: str,
        usuario_id: str,
        mensaje: str,
        respuesta: str,
        *,
        ruta: str = "",
        fuente: str = "",
        faq_id: str | None = None,
        similitud: float | None = None,
        tokens: tuple[int, int] = (0, 0),
        latencia_ms: int = 0,
        herramientas: list[dict[str, Any]] | None = None,
    ) -> None:
        ahora = _ahora()
        filas = [
            (
                f"MSG-{uuid.uuid4().hex[:12]}",
                turno_id,
                sesion_id,
                usuario_id,
                "user",
                mensaje,
                ruta,
                fuente,
                faq_id,
                similitud,
                0,
                0,
                0,
                None,
                ahora,
            ),
            (
                f"MSG-{uuid.uuid4().hex[:12]}",
                turno_id,
                sesion_id,
                usuario_id,
                "assistant",
                respuesta,
                ruta,
                fuente,
                faq_id,
                similitud,
                int(tokens[0]),
                int(tokens[1]),
                int(latencia_ms),
                json.dumps(herramientas or [], ensure_ascii=False, default=str),
                ahora,
            ),
        ]
        with self.conectar() as con:
            con.executemany(
                "insert into mensajes (id, turno_id, sesion_id, usuario_id, rol, "
                "contenido_redactado, ruta, fuente, faq_id, similitud, tokens_entrada, "
                "tokens_salida, latencia_ms, herramientas_json, creado_en) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                filas,
            )

    def mensajes_de(self, sesion_id: str, limite: int = 50) -> list[dict[str, Any]]:
        """Últimos `limite` mensajes de la sesión, en orden de inserción."""
        with self.conectar() as con:
            filas = con.execute(
                "select * from mensajes where sesion_id = ? order by rowid desc limit ?",
                (sesion_id, max(0, limite)),
            ).fetchall()
        return [dict(f) for f in reversed(filas)]

    def contar_turnos(self, sesion_id: str) -> int:
        with self.conectar() as con:
            fila = con.execute(
                "select count(*) from mensajes where sesion_id = ? and rol = 'assistant'",
                (sesion_id,),
            ).fetchone()
        return int(fila[0])

    def borrar_sesion(self, sesion_id: str) -> int:
        with self.conectar() as con:
            cursor = con.execute("delete from mensajes where sesion_id = ?", (sesion_id,))
        return int(cursor.rowcount)

    def metricas(self) -> dict[str, Any]:
        with self.conectar() as con:
            filas = con.execute(
                "select ruta, count(*) as turnos, sum(tokens_entrada) as entrada, "
                "sum(tokens_salida) as salida, avg(latencia_ms) as latencia "
                "from mensajes where rol = 'assistant' group by ruta order by turnos desc"
            ).fetchall()
            latencias = [
                int(f[0])
                for f in con.execute(
                    "select latencia_ms from mensajes where rol = 'assistant' "
                    "and latencia_ms > 0 order by latencia_ms"
                ).fetchall()
            ]

        def percentil(valores: list[int], p: float) -> int:
            if not valores:
                return 0
            posicion = min(len(valores) - 1, round((len(valores) - 1) * p))
            return valores[posicion]

        por_ruta = [dict(f) for f in filas]
        return {
            "por_ruta": por_ruta,
            "turnos": sum(f["turnos"] for f in por_ruta),
            "tokens_entrada": sum(f["entrada"] or 0 for f in por_ruta),
            "tokens_salida": sum(f["salida"] or 0 for f in por_ruta),
            "latencia_p50": percentil(latencias, 0.50),
            "latencia_p95": percentil(latencias, 0.95),
        }
