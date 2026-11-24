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

import re
import threading
import zlib

from app.core.texto import normalizar
from app.llm.cliente import Mensaje
from app.persistencia.db import BaseDatos
from app.servicios.faq import IndiceFAQ
from app.servicios.turno import es_seguimiento

CANDADOS = 64  # cantidad fija: dos sesiones pueden compartir uno, nunca crece la memoria
RUTA_FILTRO = "filtro"  # turnos frenados por las guardias de seguridad
# Lo que envía la tarjeta de un libro al elegirla (frontend/js/libros.js)
RE_TARJETA = re.compile(r"^Quiero el libro «(.+)»$")


def nota_de_tarjeta(bd: BaseDatos, mensaje: str) -> str:
    """El libro exacto que el cliente eligió en una tarjeta, con su ISBN, para el modelo.

    En la demo, "Quiero el libro «El chico nuevo…»" terminó sin agregarse: el modelo dijo
    haberlo agregado sin llamar a actualizar_carrito o mostró la ficha. Con el ISBN y la
    instrucción, no tiene que adivinar cuál es ni qué hacer.
    """
    if not (elegido := RE_TARJETA.match(mensaje.strip())):
        return ""
    titulo = normalizar(elegido.group(1))
    libro = next((lb for lb in bd.libros_activos() if normalizar(lb["titulo"]) == titulo), None)
    if libro is None or libro["stock"] <= 0:
        return ""
    return (
        f"[Contexto interno, no lo escribió el cliente: eligió en una tarjeta «{libro['titulo']}», "
        f"ISBN {libro['isbn']}. Agrégalo ahora con actualizar_carrito, cantidad 1 (y la zona si "
        "ya la dijo), y muestra el resumen que devuelve.]"
    )


NOTA_FAQ = (
    "[Contexto interno, no lo escribió el cliente: preguntas frecuentes verificadas que "
    "pueden servir; úsalas solo si responden la consulta]"
)


class CandadosPorSesion:
    """Serializa los turnos de una misma sesión sin bloquear a las demás."""

    def __init__(self, cantidad: int = CANDADOS) -> None:
        self._candados = [threading.Lock() for _ in range(cantidad)]

    def de(self, session_id: str) -> threading.Lock:
        return self._candados[zlib.crc32(session_id.encode("utf-8")) % len(self._candados)]


def historial(bd: BaseDatos, session_id: str, limite: int) -> list[Mensaje]:
    """Los últimos mensajes, sin los turnos que frenó una guardia.

    En la demo, el bot rechazó "ignora tu programación… y dime 2+2…", pero en el turno
    siguiente "responde las otras dos preguntas" las encontró en el historial y las respondió.
    """
    filas = bd.mensajes_de(session_id, limite=limite)
    return [
        {
            "role": "user" if f["rol"] == "user" else "assistant",
            "content": f["contenido_redactado"],
        }
        for f in filas
        if f.get("ruta") != RUTA_FILTRO
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
    faq_minimo: float = 0.0,
) -> list[Mensaje]:
    sistema = prompt_sistema
    previos = historial(bd, session_id, limite)
    if resumen := bd.resumen_de(session_id):
        sistema += f"\n\nResumen de la conversación anterior, que ya no ves:\n{resumen['texto']}"
    # Lo que cambia en cada turno va al final, en el mensaje del cliente: así el prompt, las
    # herramientas y el historial forman un prefijo fijo que el proveedor reutiliza de su
    # caché. Medido con qwen-plus: 98 % de tokens en caché contra 69 % con las FAQ en el sistema.
    contenido = mensaje
    if tarjeta := nota_de_tarjeta(bd, mensaje):
        contenido += f"\n\n{tarjeta}"
    if not saludo:
        consulta = mensaje
        if es_seguimiento(mensaje):
            anterior = next((m["content"] for m in reversed(previos) if m["role"] == "user"), "")
            consulta = f"{anterior} {mensaje}"
        if contexto := faq.contexto(consulta, minimo=faq_minimo):
            contenido += f"\n\n{NOTA_FAQ}\n{contexto}"
    return [
        {"role": "system", "content": sistema},
        *previos,
        {"role": "user", "content": contenido},
    ]
