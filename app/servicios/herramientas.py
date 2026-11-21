"""Herramientas que el LLM puede ejecutar (function calling).

Cada herramienta declara su esquema JSON y su ejecutor. Los ejecutores nunca
lanzan excepciones: devuelven `{"error": ...}` para que el modelo explique el
problema al cliente en lugar de romper la conversación.

El `usuario_id` no lo elige el modelo: viene de la sesión. Así un cliente no
puede reservar ni cobrar pedidos a nombre de otro cambiando un argumento.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.persistencia.db import BaseDatos, ErrorPedido
from app.servicios.rag import BuscadorDocumentos

ESQUEMAS: list[dict[str, Any]] = [
    {
        "name": "buscar_productos",
        "description": "Busca productos del catálogo por nombre, categoría o código. "
        "Usarla antes de reservar si no se conoce el código exacto.",
        "parameters": {
            "type": "object",
            "properties": {
                "consulta": {
                    "type": "string",
                    "description": "Texto libre: 'laptop', 'monitor 27', 'LAPTOP001'",
                },
                "categoria": {"type": "string", "description": "Categoría exacta, opcional"},
                "limite": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["consulta"],
        },
    },
    {
        "name": "consultar_stock",
        "description": "Devuelve cuántas unidades quedan de un producto concreto (por código).",
        "parameters": {
            "type": "object",
            "properties": {"codigo": {"type": "string", "description": "Código del producto"}},
            "required": ["codigo"],
        },
    },
    {
        "name": "reservar_pedido",
        "description": "Reserva unidades de un producto para el cliente actual. Descuenta stock "
        "y crea el pedido. Solo con producto y cantidad confirmados por el cliente.",
        "parameters": {
            "type": "object",
            "properties": {
                "codigo": {"type": "string", "description": "Código del producto"},
                "cantidad": {"type": "integer", "minimum": 1, "description": "Unidades"},
            },
            "required": ["codigo", "cantidad"],
        },
    },
    {
        "name": "consultar_pedido",
        "description": "Devuelve el estado, el total y el detalle de un pedido por su id.",
        "parameters": {
            "type": "object",
            "properties": {"pedido_id": {"type": "string", "description": "Ej. PED-1A2B3C4D"}},
            "required": ["pedido_id"],
        },
    },
    {
        "name": "cancelar_pedido",
        "description": "Cancela un pedido reservado y devuelve las unidades al stock.",
        "parameters": {
            "type": "object",
            "properties": {"pedido_id": {"type": "string"}},
            "required": ["pedido_id"],
        },
    },
    {
        "name": "cobrar_pedido",
        "description": "Cobra un pedido reservado del cliente actual. Métodos: tarjeta, "
        "efectivo o transferencia. Un pedido no se cobra dos veces.",
        "parameters": {
            "type": "object",
            "properties": {
                "pedido_id": {"type": "string", "description": "Ej. PED-1A2B3C4D"},
                "metodo": {"type": "string", "enum": ["tarjeta", "efectivo", "transferencia"]},
            },
            "required": ["pedido_id", "metodo"],
        },
    },
    {
        "name": "buscar_politica",
        "description": "Busca en los documentos de la empresa: garantías, devoluciones, "
        "envíos, derechos del consumidor y manuales de los equipos.",
        "parameters": {
            "type": "object",
            "properties": {"pregunta": {"type": "string"}},
            "required": ["pregunta"],
        },
    },
]

IDEMPOTENTES = frozenset(
    {"buscar_productos", "consultar_stock", "consultar_pedido", "buscar_politica"}
)
ACCIONES = frozenset({"reservar_pedido", "cobrar_pedido", "cancelar_pedido"})

Resultado = dict[str, Any]


class Herramientas:
    """Ejecuta herramientas por nombre, inyectando el usuario de la sesión."""

    def __init__(self, bd: BaseDatos, buscador: BuscadorDocumentos) -> None:
        self.bd = bd
        self.buscador = buscador
        self._mapa: dict[str, Callable[..., Resultado]] = {
            "buscar_productos": self._buscar_productos,
            "consultar_stock": self._consultar_stock,
            "reservar_pedido": self._reservar_pedido,
            "consultar_pedido": self._consultar_pedido,
            "cancelar_pedido": self._cancelar_pedido,
            "cobrar_pedido": self._cobrar_pedido,
            "buscar_politica": self._buscar_politica,
        }

    @staticmethod
    def esquemas() -> list[dict[str, Any]]:
        return ESQUEMAS

    def ejecutar(self, nombre: str, argumentos: dict[str, Any], usuario_id: str) -> Resultado:
        ejecutor = self._mapa.get(nombre)
        if ejecutor is None:
            return {"error": f"Herramienta desconocida: {nombre!r}"}
        try:
            return ejecutor(usuario_id=usuario_id, **(argumentos or {}))
        except ErrorPedido as exc:
            return {"error": str(exc)}
        except TypeError as exc:
            return {"error": f"Argumentos inválidos para {nombre}: {exc}"}
        except Exception as exc:  # nunca tumbar el turno por una herramienta
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- ejecutores --------------------------------------------------------------
    def _buscar_productos(
        self, consulta: str = "", categoria: str | None = None, limite: int = 5, **_: Any
    ) -> Resultado:
        productos = self.bd.buscar_productos(consulta or "", categoria, limite or 5)
        return {"encontrados": len(productos), "productos": productos}

    def _consultar_stock(self, codigo: str = "", **_: Any) -> Resultado:
        producto = self.bd.obtener_producto(codigo)
        if producto is None:
            return {"error": f"No existe el producto {codigo!r}."}
        return {
            "codigo": producto["codigo"],
            "nombre": producto["nombre"],
            "stock": producto["stock"],
            "precio": producto["precio"],
        }

    def _reservar_pedido(
        self, usuario_id: str, codigo: str = "", cantidad: int = 1, **_: Any
    ) -> Resultado:
        return self.bd.reservar_pedido(usuario_id, codigo, int(cantidad))

    def _consultar_pedido(self, usuario_id: str, pedido_id: str = "", **_: Any) -> Resultado:
        pedido = self.bd.consultar_pedido(pedido_id)
        if pedido is None or pedido["usuario_id"] != usuario_id:
            # mismo mensaje en ambos casos: no se revela si existe un pedido ajeno
            return {"error": f"No existe el pedido {pedido_id!r} para este cliente."}
        return pedido

    def _cancelar_pedido(self, usuario_id: str, pedido_id: str = "", **_: Any) -> Resultado:
        pedido = self.bd.consultar_pedido(pedido_id)
        if pedido is None:
            return {"error": f"No existe el pedido {pedido_id!r}."}
        if pedido["usuario_id"] != usuario_id:
            return {"error": "Ese pedido pertenece a otro cliente."}
        return self.bd.cancelar_pedido(pedido_id)

    def _cobrar_pedido(
        self, usuario_id: str, pedido_id: str = "", metodo: str = "tarjeta", **_: Any
    ) -> Resultado:
        return self.bd.registrar_pago(usuario_id, pedido_id, metodo)

    def _buscar_politica(self, pregunta: str = "", **_: Any) -> Resultado:
        hallazgos = self.buscador.buscar(pregunta, k=self.buscador.parametros.k_final)
        if not hallazgos:
            return {"error": "No hay documentos relacionados con esa consulta."}
        return {"hallazgos": hallazgos}
