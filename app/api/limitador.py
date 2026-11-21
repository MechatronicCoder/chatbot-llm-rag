"""Límite de peticiones por cliente con ventana deslizante en memoria.

Protege el crédito de la API del LLM cuando la URL es pública. Vive en memoria
porque el servicio corre con una sola réplica; con varias réplicas el conteo
debería moverse a un almacén compartido (por ejemplo, Redis).
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class LimitadorPeticiones:
    def __init__(
        self,
        maximo: int,
        ventana_s: float = 60.0,
        *,
        reloj: Callable[[], float] = time.monotonic,
        max_clientes: int = 10_000,
    ) -> None:
        self.maximo = maximo
        self.ventana_s = ventana_s
        self._reloj = reloj
        self._max_clientes = max_clientes
        self._marcas: dict[str, deque[float]] = {}
        self._candado = threading.Lock()

    @property
    def activo(self) -> bool:
        return self.maximo > 0

    def permitir(self, cliente: str) -> tuple[bool, int]:
        """Devuelve `(permitido, segundos_para_reintentar)`."""
        if not self.activo:
            return True, 0
        ahora = self._reloj()
        with self._candado:
            marcas = self._marcas.setdefault(cliente, deque())
            while marcas and ahora - marcas[0] >= self.ventana_s:
                marcas.popleft()
            if len(marcas) >= self.maximo:
                espera = math.ceil(self.ventana_s - (ahora - marcas[0]))
                return False, max(1, espera)
            marcas.append(ahora)
            if len(self._marcas) > self._max_clientes:
                self._purgar(ahora)
            return True, 0

    def _purgar(self, ahora: float) -> None:
        """Olvida clientes sin actividad reciente para que la memoria no crezca."""
        inactivos = [c for c, m in self._marcas.items() if not m or ahora - m[-1] >= self.ventana_s]
        for cliente in inactivos:
            del self._marcas[cliente]
