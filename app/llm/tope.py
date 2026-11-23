"""Tope de gasto: el modelo no se llama si el consumo de 24 horas pasó el límite.

El servicio publicado tiene login y un límite de mensajes por minuto, pero eso no
acota la factura de un día entero. Antes de cada llamada se suman los tokens de
las últimas 24 horas; si pasan el tope, se lanza `SinProveedores` y el turno sigue
en modo demo, igual que cuando no hay modelo configurado.

El conteo sale de la base de mensajes: se reinicia si el contenedor se recrea.
El límite firme es el que se fija en la consola del proveedor.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.llm.cliente import Mensaje, ModeloChat, RespuestaLLM, SinProveedores

AVISO_TOPE = (
    "Se alcanzó el límite diario de uso del modelo: respondo con el catálogo y las "
    "preguntas frecuentes hasta que se libere."
)


class ModeloConTope:
    """Envuelve un modelo y deja de llamarlo al pasar el tope de tokens en 24 horas."""

    def __init__(self, modelo: ModeloChat, consumo: Callable[[], int], tope: int) -> None:
        self.modelo = modelo
        self.consumo = consumo  # tokens gastados en las últimas 24 horas
        self.tope = tope  # 0 = sin tope

    @property
    def configurado(self) -> bool:
        return self.modelo.configurado

    @property
    def modelo_principal(self) -> str:
        return self.modelo.modelo_principal

    @property
    def agotado(self) -> bool:
        return bool(self.tope) and self.consumo() >= self.tope

    def cerrar(self) -> None:
        cerrar = getattr(self.modelo, "cerrar", None)
        if callable(cerrar):
            cerrar()

    def chat(
        self,
        mensajes: list[Mensaje],
        herramientas: Sequence[dict[str, Any]] | None = None,
        *,
        temperatura: float = 0.3,
        max_tokens: int = 700,
    ) -> RespuestaLLM:
        if self.agotado:
            raise SinProveedores(AVISO_TOPE)
        return self.modelo.chat(
            mensajes, herramientas, temperatura=temperatura, max_tokens=max_tokens
        )
