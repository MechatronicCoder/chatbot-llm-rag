"""Esquemas JSON de las herramientas que el modelo puede llamar (function calling).

Las descripciones son instrucciones para el modelo: cuándo usar cada herramienta
importa tanto como sus parámetros.
"""

from __future__ import annotations

from typing import Any

_ISBN = {
    "type": "string",
    "description": "ISBN-13 del libro, tal como lo devolvió otra herramienta",
}
_PEDIDO = {"type": "string", "description": "Id del pedido, por ejemplo PED-1A2B3C4D"}
_EDAD = {"type": "integer", "minimum": 0, "maximum": 99, "description": "Edad del lector"}
_PRECIO_MAX = {"type": "number", "minimum": 0, "description": "Presupuesto máximo en soles"}
_PRECIO_MIN = {"type": "number", "minimum": 0, "description": "Precio mínimo en soles"}

ESQUEMAS: list[dict[str, Any]] = [
    {
        "name": "buscar_libros",
        "description": "Busca en el catálogo cuando el cliente nombra un título, un autor, una "
        "saga, una editorial o un ISBN concreto. Devuelve precio y stock. Tolera errores de "
        "tipeo (`correcciones` dice cómo se entendió). Si encuentra menos de tres libros, agrega "
        "`relacionados`: parecidos a lo pedido, pero no lo que pidió. Con consulta vacía (o un "
        "género) y `orden`, lista el catálogo ordenado: úsala para 'los mejor valorados', 'los "
        "más baratos' o 'los más caros'.",
        "parameters": {
            "type": "object",
            "properties": {
                "consulta": {"type": "string", "description": "Título, autor, saga o ISBN"},
                "categoria": {"type": "string", "description": "Categoría exacta, opcional"},
                "autor": {"type": "string", "description": "Nombre del autor, opcional"},
                "edad": _EDAD,
                "precio_max": _PRECIO_MAX,
                "precio_min": _PRECIO_MIN,
                "limite": {"type": "integer", "minimum": 1, "maximum": 10},
                "orden": {
                    "type": "string",
                    "enum": ["valoracion", "precio_asc", "precio_desc"],
                    "description": "Mejor valorados primero (por defecto), más baratos o más caros",
                },
            },
            "required": ["consulta"],
        },
    },
    {
        "name": "recomendar_libros",
        "description": "Recomienda libros del catálogo cuando el cliente describe lo que busca "
        "(tema, gusto, para qué, parecido a otro libro, tipo de protagonista). Solo devuelve "
        "libros con stock, ordenados por parecido y valoración. Si es para un niño o un "
        "joven y no se conoce la edad, preguntarla antes de llamar a esta herramienta.",
        "parameters": {
            "type": "object",
            "properties": {
                "necesidad": {
                    "type": "string",
                    "description": "Lo que busca el cliente, con sus palabras y con todo lo que "
                    "se sabe: género, temas, tono, protagonista, libros que le gustaron",
                },
                "edad": _EDAD,
                "categoria": {"type": "string", "description": "Categoría exacta, opcional"},
                "precio_max": _PRECIO_MAX,
                "precio_min": _PRECIO_MIN,
                "excluir": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "ISBN ya ofrecidos o ya leídos por el cliente",
                },
                "cantidad": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["necesidad"],
        },
    },
    {
        "name": "ficha_libro",
        "description": "Devuelve la ficha completa de un libro (sinopsis, páginas, formato, "
        "edad, valoración, stock) y libros similares disponibles para sugerir.",
        "parameters": {
            "type": "object",
            "properties": {"isbn": _ISBN},
            "required": ["isbn"],
        },
    },
    {
        "name": "actualizar_carrito",
        "description": "Agrega, cambia o quita libros del carrito del cliente y fija la zona de "
        "envío. Devuelve el carrito con subtotal, envío y total calculados por el sistema y un "
        "`resumen` listo para mostrar: nunca sumes ni calcules el envío tú. Sin argumentos, "
        "devuelve el carrito actual.",
        "parameters": {
            "type": "object",
            "properties": {
                "cambios": {
                    "type": "array",
                    "description": "Un elemento por libro, con la cantidad FINAL de ese libro en "
                    "el carrito (0 lo quita). Los libros que no se nombran quedan igual.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "isbn": _ISBN,
                            "cantidad": {"type": "integer", "minimum": 0, "maximum": 50},
                        },
                        "required": ["isbn", "cantidad"],
                    },
                },
                "zona": {
                    "type": "string",
                    "enum": ["lima", "provincias"],
                    "description": "lima = Lima Metropolitana; provincias = resto del país",
                },
            },
        },
    },
    {
        "name": "reservar_pedido",
        "description": "Reserva el carrito del cliente: descuenta stock y fija los montos. "
        "Necesita libros en el carrito y la zona de envío. No cobra.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "consultar_pedido",
        "description": "Devuelve el estado, el total y el detalle de un pedido del cliente.",
        "parameters": {
            "type": "object",
            "properties": {"pedido_id": _PEDIDO},
            "required": ["pedido_id"],
        },
    },
    {
        "name": "cancelar_pedido",
        "description": "Cancela un carrito o un pedido reservado; lo reservado vuelve al stock.",
        "parameters": {
            "type": "object",
            "properties": {"pedido_id": _PEDIDO},
            "required": ["pedido_id"],
        },
    },
    {
        "name": "derivar_a_persona",
        "description": "Pasa la atención a una persona del equipo cuando el cliente lo pide o "
        "el caso no se resuelve con las herramientas (un reclamo, un pedido especial, un "
        "problema con un pago). Registra el motivo y devuelve el teléfono, el correo y el "
        "horario de atención. No pidas datos personales al cliente.",
        "parameters": {
            "type": "object",
            "properties": {"motivo": {"type": "string", "description": "Qué necesita, en breve"}},
            "required": ["motivo"],
        },
    },
    {
        "name": "buscar_politica",
        "description": "Busca en los documentos de la librería: envíos, cambios y devoluciones, "
        "pagos, preventas y pedidos especiales, libro de reclamaciones y derechos del consumidor.",
        "parameters": {
            "type": "object",
            "properties": {"pregunta": {"type": "string"}},
            "required": ["pregunta"],
        },
    },
]

# Herramientas que no cambian nada: si el modelo las repite en un turno, se reutiliza el resultado.
IDEMPOTENTES = frozenset(
    {"buscar_libros", "recomendar_libros", "ficha_libro", "consultar_pedido", "buscar_politica"}
)
# El modelo no cobra: el cliente paga con el botón de la tarjeta (POST /api/pedidos/…/pago).
ACCIONES = frozenset({"actualizar_carrito", "reservar_pedido", "cancelar_pedido"})
