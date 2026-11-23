"""Lo que ve el modelo en cada turno: prompt del sistema, historial y mensaje.

El historial vive en la base y se guarda siempre redactado. Lo que ya salió de la
ventana del historial llega como resumen (`resumen.py`). Un saludo no arrastra
preguntas frecuentes al prompt; cualquier otro mensaje recibe las FAQ cercanas
como contexto, para que el modelo no improvise lo que ya está respondido. Una
pregunta de seguimiento ("¿y a provincias?") las busca junto con el mensaje
anterior del cliente, que es el que dice de qué se habla.

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
from app.servicios.turno import es_seguimiento

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
    previos = historial(bd, session_id, limite)
    if resumen := bd.resumen_de(session_id):
        sistema += f"\n\nResumen de la conversación anterior, que ya no ves:\n{resumen['texto']}"
    if not saludo:
        consulta = mensaje
        if es_seguimiento(mensaje):
            anterior = next((m["content"] for m in reversed(previos) if m["role"] == "user"), "")
            consulta = f"{anterior} {mensaje}"
        contexto = faq.contexto(consulta)
        if contexto:
            sistema += (
                "\n\nPreguntas frecuentes verificadas que pueden servir "
                f"(úsalas solo si responden la consulta):\n{contexto}"
            )
    return [
        {"role": "system", "content": sistema},
        *previos,
        {"role": "user", "content": mensaje},
    ]
