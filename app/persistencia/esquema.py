"""Esquema de la base y funciones auxiliares compartidas por los repositorios."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
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


def momento_actual() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def raiz(palabra: str) -> str:
    """'laptops' -> 'laptop', 'monitores' -> 'monitor' (suficiente para buscar)."""
    for sufijo in ("es", "s"):
        if len(palabra) > 4 and palabra.endswith(sufijo):
            return palabra[: -len(sufijo)]
    return palabra


def fila_a_producto(fila: sqlite3.Row) -> dict[str, Any]:
    return {
        "codigo": fila["codigo"],
        "nombre": fila["nombre"],
        "categoria": fila["categoria"],
        "precio": fila["precio"],
        "specs": fila["specs"],
        "stock": fila["stock"],
    }


def fila_a_pedido(fila: sqlite3.Row) -> dict[str, Any]:
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
