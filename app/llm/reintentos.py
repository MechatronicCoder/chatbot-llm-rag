"""Reintentos con espera creciente ante fallos pasajeros de una API HTTP.

Solo se repite lo que puede salir bien al rato:

- 429 (límite de uso) y 5xx (servidor caído o saturado);
- errores de red; los timeouts, solo si la política lo permite. El chat tiene un
  proveedor de respaldo y no le conviene esperar otro minuto al mismo.

Un 400 o un 401 fallarían igual la próxima vez, así que se devuelven enseguida.
La espera se duplica en cada intento, con una parte al azar para que varios
clientes no reintenten al mismo tiempo. Si el servidor manda `Retry-After`, se
respeta mientras no pase del tope; si pide más, se desiste para no colgar el turno.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

AlReintentar = Callable[[str, float], None]  # (motivo, segundos de espera)


class FalloPasajero(RuntimeError):
    """Se agotaron los intentos o el servidor pidió esperar más que el tope."""


@dataclass(frozen=True)
class PoliticaReintentos:
    reintentos: int = 2
    base_s: float = 1.0
    espera_max_s: float = 6.0
    reintentar_timeouts: bool = True

    def espera(self, intento: int, azar: float) -> float:
        """Mitad fija y mitad al azar de base·2^intento, sin pasar del tope."""
        tramo = min(self.espera_max_s, self.base_s * 2**intento)
        return tramo / 2 + azar * tramo / 2


def es_reintentable(codigo: int) -> bool:
    return codigo == 429 or codigo >= 500


def segundos_retry_after(respuesta: httpx.Response, ahora: datetime | None = None) -> float | None:
    """`Retry-After` en segundos (número o fecha HTTP); None si no viene o no se entiende."""
    valor = respuesta.headers.get("retry-after", "").strip()
    if not valor:
        return None
    try:
        return max(0.0, float(valor))
    except ValueError:
        pass
    try:
        fecha = parsedate_to_datetime(valor)
    except (TypeError, ValueError):
        return None
    if fecha.tzinfo is None:  # "-0000" se interpreta sin zona: es UTC
        fecha = fecha.replace(tzinfo=UTC)
    return max(0.0, (fecha - (ahora or datetime.now(UTC))).total_seconds())


def detalle_error(respuesta: httpx.Response) -> str:
    """El mensaje de error que devolvió la API, recortado."""
    try:
        cuerpo = respuesta.json()
        detalle = cuerpo.get("error", cuerpo) if isinstance(cuerpo, dict) else cuerpo
        if isinstance(detalle, dict):
            detalle = detalle.get("message") or detalle.get("code") or detalle
    except ValueError:
        detalle = respuesta.text
    return str(detalle)[:160]


def post_con_reintentos(
    http: httpx.Client,
    url: str,
    *,
    cuerpo: dict[str, Any],
    cabeceras: dict[str, str],
    politica: PoliticaReintentos,
    esperar: Callable[[float], None] = time.sleep,
    azar: Callable[[], float] = random.random,
    al_reintentar: AlReintentar | None = None,
) -> httpx.Response:
    """La primera respuesta definitiva: un 2xx o un error que no vale la pena repetir.

    Lanza `FalloPasajero` si los fallos pasajeros no se resolvieron a tiempo.
    """
    motivo = ""
    for intento in range(politica.reintentos + 1):
        pedida: float | None = None
        try:
            respuesta = http.post(url, json=cuerpo, headers=cabeceras)
        except httpx.HTTPError as exc:
            motivo = f"sin respuesta ({type(exc).__name__})"
            if isinstance(exc, httpx.TimeoutException) and not politica.reintentar_timeouts:
                raise FalloPasajero(motivo) from exc
        else:
            if not es_reintentable(respuesta.status_code):
                return respuesta
            motivo = f"HTTP {respuesta.status_code} {detalle_error(respuesta)}".rstrip()
            pedida = segundos_retry_after(respuesta)
            if pedida is not None and pedida > politica.espera_max_s:
                raise FalloPasajero(f"{motivo} (pide esperar {pedida:.0f} s)")
        if intento < politica.reintentos:
            espera = pedida if pedida is not None else politica.espera(intento, azar())
            if al_reintentar is not None:
                al_reintentar(motivo, espera)
            esperar(espera)
    raise FalloPasajero(motivo)
