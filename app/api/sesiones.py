"""Sesiones del acceso de la demo, en memoria y con vencimiento.

Viven en memoria porque el servicio corre con una sola réplica (igual que el
límite de peticiones). Un reinicio cierra todas las sesiones: basta con volver a
iniciar sesión.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable


class Sesiones:
    def __init__(
        self,
        duracion_s: float,
        *,
        reloj: Callable[[], float] = time.monotonic,
        maximo: int = 1000,
    ) -> None:
        self.duracion_s = duracion_s
        self._reloj = reloj
        self._maximo = maximo
        self._vigentes: dict[str, tuple[str, float]] = {}
        self._candado = threading.Lock()

    def crear(self, usuario: str) -> str:
        """Token aleatorio de 256 bits; si se llega al máximo se descarta la más antigua."""
        token = secrets.token_urlsafe(32)
        ahora = self._reloj()
        with self._candado:
            self._vigentes = {t: s for t, s in self._vigentes.items() if s[1] > ahora}
            while len(self._vigentes) >= self._maximo:
                del self._vigentes[next(iter(self._vigentes))]
            self._vigentes[token] = (usuario, ahora + self.duracion_s)
        return token

    def usuario_de(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._candado:
            sesion = self._vigentes.get(token)
            if sesion is None:
                return None
            if sesion[1] <= self._reloj():
                del self._vigentes[token]
                return None
            return sesion[0]

    def cerrar(self, token: str | None) -> None:
        if token:
            with self._candado:
                self._vigentes.pop(token, None)
