"""Base de datos SQLite: catálogo de libros, usuarios, pedidos, pagos y conversación.

Reglas que se cumplen aquí y no en el modelo de lenguaje:

- la reserva descuenta stock y crea el pedido en la misma transacción, así dos
  reservas simultáneas no venden lo mismo dos veces;
- un pedido no se cobra dos veces ni se cobra el de otro cliente;
- los mensajes se guardan siempre redactados, nunca con el dato sensible original.
"""

from __future__ import annotations

from app.persistencia.conversacion import RepositorioConversacion
from app.persistencia.esquema import METODOS_PAGO, ErrorPedido
from app.persistencia.tienda import RepositorioTienda

__all__ = ["METODOS_PAGO", "BaseDatos", "ErrorPedido"]


class BaseDatos(RepositorioTienda, RepositorioConversacion):
    """Punto de acceso único a la base: catálogo, venta y conversación.

    Cada repositorio vive en su propio módulo (`catalogo.py`, `tienda.py`,
    `conversacion.py`); esta clase los reúne para que el resto de la aplicación
    dependa de una sola.
    """
