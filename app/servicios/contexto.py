"""Lo que ve el modelo en cada turno: prompt del sistema, historial y mensaje.

El historial vive en la base y se guarda siempre redactado. Un saludo no arrastra
preguntas frecuentes al prompt; cualquier otro mensaje recibe las FAQ cercanas
como contexto, para que el modelo no improvise lo que ya está respondido.

También aquí, el candado por sesión: la API atiende varias peticiones a la vez y
dos mensajes de la misma conversación no deben armar su historial en paralelo,
porque cada uno respondería sin ver al otro.
"""

from __future__ import annotations

import threading
import zlib

from app.llm.cliente import Mensaje
from app.persistencia.db import BaseDatos
from app.servicios.faq import IndiceFAQ

CANDADOS = 64  # cantidad fija: dos sesiones pueden compartir uno, nunca crece la memoria


class CandadosPorSesion:
    """Serializa los turnos de una misma sesión sin bloquear a las demás."""

    def __init__(self, cantidad: int = CANDADOS) -> None:
        self._candados = [threading.Lock() for _ in range(cantidad)]

    def de(self, session_id: str) -> threading.Lock:
        return self._candados[zlib.crc32(session_id.encode("utf-8")) % len(self._candados)]


def historial(bd: BaseDatos, session_id: str, limite: int) -> list[Mensaje]:
    filas = bd.mensajes_de(session_id, limite=limite)
    return [
        {
            "role": "user" if f["rol"] == "user" else "assistant",
            "content": f["contenido_redactado"],
        }
        for f in filas
    ]


def mensajes_iniciales(
    prompt_sistema: str,
    faq: IndiceFAQ,
    bd: BaseDatos,
    mensaje: str,
    session_id: str,
    *,
    saludo: bool,
    limite: int,
) -> list[Mensaje]:
    sistema = prompt_sistema
    if not saludo:
        contexto = faq.contexto(mensaje)
        if contexto:
            sistema += (
                "\n\nPreguntas frecuentes verificadas que pueden servir "
                f"(úsalas solo si responden la consulta):\n{contexto}"
            )
    return [
        {"role": "system", "content": sistema},
        *historial(bd, session_id, limite),
        {"role": "user", "content": mensaje},
    ]
