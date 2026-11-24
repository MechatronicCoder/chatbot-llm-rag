"""Lo que la verificación devuelve cuando una respuesta no se puede entregar."""

from __future__ import annotations

from dataclasses import dataclass

# Va delante de cada corrección. Al corregir un detalle (un plazo, un monto), el modelo a veces
# dejaba de responder: a "¿ya está pagado mi pedido?" contestó con los comprobantes y el recojo.
NOTA = (
    "[Revisión interna; no la menciones al cliente. Corrige solo lo que se indica y responde "
    "igual lo que preguntó el cliente] "
)


@dataclass(frozen=True)
class Problema:
    motivo: str  # para el log
    instruccion: str  # lo que se le pide al modelo para corregir
    respaldo: str  # lo que ve el cliente si el modelo no corrige
