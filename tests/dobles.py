"""Dobles de prueba: modelo de chat y embedder falsos, sin red."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from app.llm.cliente import RespuestaLLM, SinProveedores
from app.llm.embeddings import Matriz, normalizar_filas


class ModeloFalso:
    """Devuelve respuestas preparadas en orden y registra lo que recibió."""

    def __init__(self, respuestas: list[RespuestaLLM] | None = None, fallar: bool = False) -> None:
        self.respuestas = list(respuestas or [])
        self.fallar = fallar
        self.llamadas: list[dict[str, Any]] = []

    @property
    def configurado(self) -> bool:
        return not self.fallar

    @property
    def modelo_principal(self) -> str:
        return "falso"

    def chat(
        self,
        mensajes: list[dict[str, Any]],
        herramientas: Sequence[dict[str, Any]] | None = None,
        *,
        temperatura: float = 0.3,
        max_tokens: int = 700,
    ) -> RespuestaLLM:
        self.llamadas.append(
            {"mensajes": list(mensajes), "herramientas": herramientas, "max_tokens": max_tokens}
        )
        if self.fallar:
            raise SinProveedores("sin modelo configurado (prueba)")
        if self.respuestas:
            return self.respuestas.pop(0)
        return respuesta("respuesta de prueba")


def respuesta(texto: str = "", tool_calls: list[dict[str, Any]] | None = None) -> RespuestaLLM:
    return RespuestaLLM(
        texto=texto,
        tool_calls=tool_calls or [],
        modelo="falso",
        proveedor="prueba",
        uso={"prompt_tokens": 100, "completion_tokens": 20},
        latencia_s=0.05,
    )


def llamada(nombre: str, **argumentos: Any) -> dict[str, Any]:
    return {"id": f"call-{nombre}", "name": nombre, "arguments": argumentos}


class EmbedderFalso:
    """Bolsa de palabras con vocabulario fijo: suficiente para probar el flujo denso."""

    VOCABULARIO = (
        "envio",
        "cambiar",
        "paginas",
        "impresas",
        "reclamo",
        "plazo",
        "soles",
        "comprobante",
    )

    def __init__(self, fallar: bool = False) -> None:
        self.fallar = fallar
        self.llamadas = 0

    @property
    def identificador(self) -> str:
        return "falso:10"

    def embeber(self, textos: Sequence[str]) -> Matriz:
        self.llamadas += 1
        if self.fallar:
            raise RuntimeError("API de embeddings caída (prueba)")
        import unicodedata

        filas = []
        for texto in textos:
            plano = "".join(
                c
                for c in unicodedata.normalize("NFD", texto.lower())
                if unicodedata.category(c) != "Mn"
            )
            filas.append([plano.count(p) + 0.01 for p in self.VOCABULARIO])
        return normalizar_filas(np.asarray(filas, dtype=np.float32))
