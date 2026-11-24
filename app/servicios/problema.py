"""Lo que la verificación devuelve cuando una respuesta no se puede entregar."""

from __future__ import annotations

from dataclasses import dataclass

NOTA = "[Revisión interna; no la menciones al cliente] "


@dataclass(frozen=True)
class Problema:
    motivo: str  # para el log
    instruccion: str  # lo que se le pide al modelo para corregir
    respaldo: str  # lo que ve el cliente si el modelo no corrige
