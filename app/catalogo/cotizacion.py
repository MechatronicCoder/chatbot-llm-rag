"""Cotización de un pedido: subtotal, envío y total los calcula el código.

Un modelo de lenguaje suma mal y redondea a su manera. Aquí las cuentas se hacen
en céntimos enteros y la herramienta devuelve un resumen ya redactado, que el
modelo solo tiene que mostrar.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.catalogo.carga import Tienda, ZonaEnvio

ESTADOS = {
    "carrito": "carrito (sin reservar)",
    "reservado": "reservado (falta pagar)",
    "pagado": "pagado",
    "cancelado": "cancelado",
}


def a_centimos(valor: float | Decimal | str) -> int:
    """Soles a céntimos enteros, redondeando la mitad hacia arriba (59.905 -> 5991)."""
    return int((Decimal(str(valor)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def a_soles(centimos: int) -> float:
    return float(Decimal(centimos) / 100)


@dataclass(frozen=True)
class Linea:
    isbn: str
    titulo: str
    autor: str
    cantidad: int
    precio_cent: int

    @property
    def importe_cent(self) -> int:
        return self.precio_cent * self.cantidad


@dataclass(frozen=True)
class Cotizacion:
    lineas: tuple[Linea, ...]
    zona: str | None
    subtotal_cent: int
    envio_cent: int | None  # None: falta elegir la zona o no hay nada que enviar
    falta_gratis_cent: int | None  # cuánto falta para el envío gratis, si aplica

    @property
    def total_cent(self) -> int:
        return self.subtotal_cent + (self.envio_cent or 0)


def costo_envio_cent(subtotal_cent: int, tarifa: ZonaEnvio) -> int:
    if tarifa.gratis_desde is not None and subtotal_cent >= a_centimos(tarifa.gratis_desde):
        return 0
    return a_centimos(tarifa.costo)


def cotizar(lineas: Sequence[Linea], zona: str | None, tienda: Tienda) -> Cotizacion:
    """Subtotal, envío según la zona y lo que falta para el envío gratis."""
    subtotal = sum(linea.importe_cent for linea in lineas)
    tarifa = tienda.envio.get(zona) if zona else None
    envio = costo_envio_cent(subtotal, tarifa) if tarifa is not None and lineas else None
    falta = None
    if envio and tarifa is not None and tarifa.gratis_desde is not None:
        falta = a_centimos(tarifa.gratis_desde) - subtotal
    return Cotizacion(tuple(lineas), zona if tarifa else None, subtotal, envio, falta)


def detalle(cotizacion: Cotizacion, tienda: Tienda) -> dict[str, Any]:
    """La cotización en soles, lista para la API y para el modelo."""
    zona = cotizacion.zona
    return {
        "zona": zona,
        "zona_nombre": tienda.envio[zona].nombre if zona else None,
        "lineas": [
            {
                "isbn": linea.isbn,
                "titulo": linea.titulo,
                "autor": linea.autor,
                "cantidad": linea.cantidad,
                "precio_unitario": a_soles(linea.precio_cent),
                "importe": a_soles(linea.importe_cent),
            }
            for linea in cotizacion.lineas
        ],
        "unidades": sum(linea.cantidad for linea in cotizacion.lineas),
        "subtotal": a_soles(cotizacion.subtotal_cent),
        "envio": None if cotizacion.envio_cent is None else a_soles(cotizacion.envio_cent),
        "total": a_soles(cotizacion.total_cent),
        "falta_para_envio_gratis": (
            None if cotizacion.falta_gratis_cent is None else a_soles(cotizacion.falta_gratis_cent)
        ),
    }


def _linea_envio(pedido: dict[str, Any], tienda: Tienda) -> str:
    p = tienda.precio
    if pedido["zona"] is None:
        opciones = "; ".join(
            f"{t.nombre}: {p(t.costo)}"
            + (f", gratis desde {p(t.gratis_desde)}" if t.gratis_desde is not None else "")
            for t in tienda.envio.values()
        )
        return f"Envío: falta elegir la zona ({opciones})"
    if not pedido["envio"]:
        return f"Envío a {pedido['zona_nombre']}: gratis"
    texto = f"Envío a {pedido['zona_nombre']}: {p(pedido['envio'])}"
    if pedido["falta_para_envio_gratis"]:
        texto += f" (faltan {p(pedido['falta_para_envio_gratis'])} para el envío gratis)"
    return texto


def resumen(pedido: dict[str, Any], tienda: Tienda) -> str:
    """Texto del pedido que el modelo copia tal cual: nunca hace las cuentas él."""
    p = tienda.precio
    filas = [f"Pedido {pedido['pedido_id']} · {ESTADOS.get(pedido['estado'], pedido['estado'])}"]
    if not pedido["lineas"]:
        return filas[0] + "\nEl carrito está vacío."
    filas += [
        f"- {ln['cantidad']} × «{ln['titulo']}» de {ln['autor']}: "
        + (
            f"{p(ln['precio_unitario'])} c/u = {p(ln['importe'])}"
            if ln["cantidad"] > 1
            else p(ln["importe"])
        )
        for ln in pedido["lineas"]
    ]
    filas += [f"Subtotal: {p(pedido['subtotal'])}", _linea_envio(pedido, tienda)]
    filas.append(f"Total: {p(pedido['total'])}" + ("" if pedido["zona"] else " sin envío"))
    return "\n".join(filas)


def montos_en_centimos(pedido: dict[str, Any]) -> set[int]:
    """Todos los montos de un pedido, para verificar los que escribe el modelo."""
    valores: Iterable[Any] = [
        pedido.get("subtotal"),
        pedido.get("envio"),
        pedido.get("total"),
        pedido.get("falta_para_envio_gratis"),
        *(ln[c] for ln in pedido.get("lineas") or [] for c in ("precio_unitario", "importe")),
    ]
    return {a_centimos(v) for v in valores if v is not None}
