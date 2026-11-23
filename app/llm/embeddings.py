"""Embeddings por API (formato OpenAI `/embeddings`), sin modelos locales ni GPU.

Por defecto apunta a Qwen `text-embedding-v4` en Alibaba Cloud Model Studio,
que acepta hasta 10 textos por llamada. Los vectores se devuelven normalizados
(norma 1), así la similitud coseno es un producto punto.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import httpx
import numpy as np

from app.core.config import Settings
from app.llm.reintentos import FalloPasajero, PoliticaReintentos, post_con_reintentos

Matriz = np.ndarray[Any, np.dtype[np.float32]]


class ErrorEmbeddings(RuntimeError):
    """La API de embeddings no respondió o respondió algo inesperado."""


class Embedder(Protocol):
    """Lo que el buscador necesita para vectorizar textos."""

    @property
    def identificador(self) -> str: ...

    def embeber(self, textos: Sequence[str]) -> Matriz: ...


def normalizar_filas(matriz: Matriz) -> Matriz:
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    normas[normas == 0] = 1.0
    return (matriz / normas).astype(np.float32)


class ClienteEmbeddings:
    """Cliente HTTP con lotes y reintentos ante 429, 5xx o errores de red."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        modelo: str,
        *,
        dimensiones: int = 0,
        lote: int = 10,
        timeout_s: float = 30.0,
        politica: PoliticaReintentos | None = None,
        transporte: httpx.BaseTransport | None = None,
        esperar: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.modelo = modelo
        self.dimensiones = dimensiones
        self.lote = lote
        self.politica = politica or PoliticaReintentos()
        self._esperar = esperar
        self._http = httpx.Client(timeout=timeout_s, transport=transporte)

    @classmethod
    def desde_config(cls, ajustes: Settings) -> ClienteEmbeddings:
        return cls(
            ajustes.embeddings_base_url,
            ajustes.embeddings_api_key.get_secret_value(),
            ajustes.embeddings_model,
            dimensiones=ajustes.embeddings_dimensiones,
            lote=ajustes.embeddings_lote,
            timeout_s=ajustes.embeddings_timeout_s,
            politica=PoliticaReintentos(
                reintentos=ajustes.api_reintentos, espera_max_s=ajustes.api_espera_max_s
            ),
        )

    @property
    def identificador(self) -> str:
        """Identifica el espacio vectorial: si cambia, la caché deja de servir."""
        return f"{self.modelo}:{self.dimensiones or 'defecto'}"

    def cerrar(self) -> None:
        self._http.close()

    def _pedir(self, textos: Sequence[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "model": self.modelo,
            "input": list(textos),
            "encoding_format": "float",
        }
        if self.dimensiones:
            payload["dimensions"] = self.dimensiones

        # Sin proveedor de respaldo: aquí también se reintenta un timeout.
        try:
            respuesta = post_con_reintentos(
                self._http,
                f"{self.base_url}/embeddings",
                cuerpo=payload,
                cabeceras={"Authorization": f"Bearer {self.api_key}"},
                politica=self.politica,
                esperar=self._esperar,
            )
        except FalloPasajero as exc:
            raise ErrorEmbeddings(f"la API de embeddings no respondió: {exc}") from exc
        if respuesta.status_code != 200:
            raise ErrorEmbeddings(f"HTTP {respuesta.status_code}: {respuesta.text[:160]}")
        try:
            datos = sorted(respuesta.json()["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in datos]
        except (ValueError, KeyError, TypeError) as exc:
            raise ErrorEmbeddings(f"respuesta inválida ({type(exc).__name__})") from exc

    def embeber(self, textos: Sequence[str]) -> Matriz:
        if not textos:
            return np.zeros((0, max(self.dimensiones, 1)), dtype=np.float32)
        vectores: list[list[float]] = []
        for i in range(0, len(textos), self.lote):
            lote = textos[i : i + self.lote]
            obtenidos = self._pedir(lote)
            if len(obtenidos) != len(lote):
                raise ErrorEmbeddings("la API devolvió una cantidad distinta de vectores")
            vectores.extend(obtenidos)
        return normalizar_filas(np.asarray(vectores, dtype=np.float32))
