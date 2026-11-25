"""Tarjetas que el cliente escribe en el chat: se guardan para pagar y se tapan.

Si el mensaje trae un número de tarjeta válido, se guarda la marca y los últimos cuatro
dígitos como medio de pago del cliente, y el número se reemplaza por una marca antes de
llegar a la base, al log o al modelo. El modelo solo ve una nota ("Visa •••• 4242
guardada") para avisarle al cliente que ya puede elegirla al pagar con el botón.
"""

from __future__ import annotations

import re
from typing import Any

from app.catalogo.cotizacion import resumen
from app.core import seguridad
from app.core.tarjetas import tarjetas_en
from app.persistencia.db import BaseDatos

# "págalo", "con transferencia bancaria", "pago con tarjeta", "¿cómo pago?"
RE_QUIERE_PAGAR = re.compile(
    r"\b(?:pag(?:ar|o|a|alo|álo|aré|arlo|arla)|transferencia|efectivo|tarjeta)\b", re.IGNORECASE
)


def redactar_y_guardar(
    bd: BaseDatos, usuario_id: str, mensaje: str
) -> tuple[str, list[str], list[str]]:
    """`(mensaje_redactado, hallazgos, tarjetas_guardadas)`; las tarjetas van como etiqueta."""
    guardadas = [
        bd.guardar_tarjeta(usuario_id, numero)["etiqueta"] for numero in tarjetas_en(mensaje)
    ]
    limpio, tipos = seguridad.redactar_datos_sensibles(mensaje)
    if guardadas:
        limpio += (
            "\n[Tarjeta guardada para pagar con el botón «Pagar» del pedido: "
            f"{', '.join(guardadas)}]"
        )
    return limpio, tipos, guardadas


def pedido_para_pagar(bd: BaseDatos, usuario_id: str, mensaje: str) -> dict[str, Any] | None:
    """Si el cliente quiere pagar, su carrito abierto o su último pedido reservado.

    Así la respuesta trae la tarjeta del pedido con el botón «Pagar»: en la demo, a
    "con transferencia bancaria" el bot contestaba "pulsa Pagar", pero el botón había
    quedado en un mensaje anterior.
    """
    if not RE_QUIERE_PAGAR.search(mensaje):
        return None
    pedido = bd.carrito_de(usuario_id)
    if pedido is None or not pedido["lineas"]:
        pedido = next((p for p in bd.pedidos_de(usuario_id) if p["estado"] == "reservado"), None)
    return {**pedido, "resumen": resumen(pedido, bd.tienda)} if pedido else None
