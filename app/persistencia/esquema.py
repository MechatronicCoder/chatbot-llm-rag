"""Esquema de la base y funciones auxiliares compartidas por los repositorios."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

# Se incrementa cada vez que cambia una tabla. Una base con otra versión se
# reconstruye al arrancar: guarda datos de demostración, no históricos.
ESQUEMA_VERSION = 4

TABLAS = (
    "pagos", "pedido_lineas", "pedidos", "mensajes", "resumenes", "consumo_extra", "usuarios",
    "libros", "productos",
)  # fmt: skip

ESQUEMA = """
create table if not exists libros (
    isbn             text primary key,
    titulo           text not null,
    autor            text not null,
    editorial        text not null default '',
    anio             integer,
    categoria        text not null,
    genero           text not null default '',
    temas            text not null default '[]',     -- lista JSON
    protagonista     text not null default '',
    tono             text not null default '',
    edad_min         integer,
    edad_max         integer,
    sinopsis         text not null default '',
    formato          text not null default '',
    paginas          integer,
    idioma           text not null default 'español',
    valoracion       real,
    num_valoraciones integer not null default 0,
    similares        text not null default '[]',     -- lista JSON de ISBN
    precio           real not null,
    stock            integer not null,
    portada          text,
    activo           integer not null default 1      -- 0: ya no está en el catálogo
);
create index if not exists idx_libros_categoria on libros(categoria);
create table if not exists usuarios (
    id          text primary key,
    nombre      text,
    email       text,
    creado_en   text not null
);
-- Un pedido empieza como carrito y pasa a reservado, pagado o cancelado.
-- Los montos se guardan en céntimos enteros: nada de redondeos de coma flotante.
create table if not exists pedidos (
    id              text primary key,
    usuario_id      text not null references usuarios(id),
    estado          text not null,          -- carrito | reservado | pagado | cancelado
    zona            text,                   -- clave de envio en tienda.json; null sin elegir
    subtotal_cent   integer not null default 0,
    envio_cent      integer,
    total_cent      integer not null default 0,
    creado_en       text not null,
    actualizado_en  text not null
);
create index if not exists idx_pedidos_usuario on pedidos(usuario_id, estado);
create table if not exists pedido_lineas (
    pedido_id   text not null references pedidos(id),
    isbn        text not null references libros(isbn),
    cantidad    integer not null check (cantidad > 0),
    precio_cent integer not null,           -- precio al agregar; se congela al reservar
    orden       integer not null,
    primary key (pedido_id, isbn)
);
create table if not exists pagos (
    id          text primary key,
    pedido_id   text not null references pedidos(id),
    metodo      text not null,              -- tarjeta | efectivo | transferencia
    monto_cent  integer not null,
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
-- Resumen encadenado de lo que ya no entra en la ventana del historial.
create table if not exists resumenes (
    sesion_id       text primary key,
    texto           text not null,
    cubiertos       integer not null,       -- mensajes de la sesión que ya resume
    actualizado_en  text not null
);
-- Tokens que no son de un turno (los resúmenes): también cuentan para el tope diario.
create table if not exists consumo_extra (
    id          integer primary key autoincrement,
    sesion_id   text not null,
    motivo      text not null,
    tokens      integer not null,
    creado_en   text not null
);
"""

# Columnas del libro que vienen del archivo del catálogo (todas menos `activo`).
COLUMNAS_LIBRO = (
    "isbn", "titulo", "autor", "editorial", "anio", "categoria", "genero", "temas",
    "protagonista", "tono", "edad_min", "edad_max", "sinopsis", "formato", "paginas",
    "idioma", "valoracion", "num_valoraciones", "similares", "precio", "stock", "portada",
)  # fmt: skip

METODOS_PAGO = ("tarjeta", "efectivo", "transferencia")


class ErrorPedido(ValueError):
    """Operación rechazada: libro inexistente, sin stock o pedido en otro estado."""


def momento_actual() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def raiz(palabra: str) -> str:
    """'novelas' -> 'novela', 'cuentos' -> 'cuento' (suficiente para buscar)."""
    for sufijo in ("es", "s"):
        if len(palabra) > 4 and palabra.endswith(sufijo):
            return palabra[: -len(sufijo)]
    return palabra


def fila_a_libro(fila: sqlite3.Row) -> dict[str, Any]:
    libro = {columna: fila[columna] for columna in COLUMNAS_LIBRO}
    libro["temas"] = json.loads(fila["temas"])
    libro["similares"] = json.loads(fila["similares"])
    return libro
