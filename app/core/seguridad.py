"""Filtros de seguridad: datos sensibles, inyección de instrucciones y salida del LLM.

Son funciones puras, sin dependencias de FastAPI ni del LLM, para que se puedan
probar de forma aislada. Las listas de palabras son un ejemplo del caso de la
tienda: en otro negocio se reemplazan por sus propias políticas de contenido.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos y sin signos, para comparar palabras clave."""
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9ñ ]+", " ", sin_acentos).strip()


# ---------------------------------------------------------------------------
# Inyección de instrucciones (prompt injection)
# ---------------------------------------------------------------------------
PATRONES_INYECCION = [
    r"ignora\s+(las\s+)?(instrucciones|reglas|indicaciones)",
    r"olvida\s+(las\s+)?(instrucciones|reglas|indicaciones)",
    r"(muestra|revela|ensena|dime|repite)\s+(tu|el)\s+(system\s*)?prompt",
    r"(actua|comportate)\s+como",
    r"modo\s+(desarrollador|developer|dios|admin)",
    r"jailbreak",
    r"prompt\s+del\s+sistema",
    r"instrucciones\s+del\s+sistema",
    r"eres\s+ahora\s+un",
]


def detectar_inyeccion(texto: str) -> str | None:
    """Devuelve el patrón detectado o None."""
    normalizado = normalizar(texto)
    for patron in PATRONES_INYECCION:
        if re.search(patron, normalizado):
            return patron
    return None


# ---------------------------------------------------------------------------
# Palabras fuera de política
# ---------------------------------------------------------------------------
PALABRAS_PROHIBIDAS = ["computech", "elecciones", "medicamentos", "pastillas", "drogas"]
PALABRAS_SALIDA = ["computech", "pastillas", "medicina", "drogas"]

RESPUESTAS_BLOQUEO = [
    "Lo siento, pero no puedo responder a esa consulta.",
    "Por ahora no puedo ayudar con ese tema.",
    "Lo siento, no tengo permitido responder ese tipo de consultas.",
    "Esa consulta queda fuera de lo que puedo atender.",
]


def detectar_palabra_prohibida(texto: str) -> str | None:
    """Devuelve la palabra prohibida encontrada o None."""
    normalizado = normalizar(texto)
    for palabra in PALABRAS_PROHIBIDAS:
        if normalizar(palabra) in normalizado:
            return palabra
    return None


def respuesta_bloqueo(motivo: str = "") -> str:
    """Respuesta estable (misma entrada, misma respuesta) para que sea comprobable."""
    indice = sum(ord(c) for c in motivo) % len(RESPUESTAS_BLOQUEO)
    return RESPUESTAS_BLOQUEO[indice]


# ---------------------------------------------------------------------------
# Datos sensibles
# ---------------------------------------------------------------------------
RE_TARJETA = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
RE_CLAVE = re.compile(
    r"\b(cvv|cvc|codigo de seguridad|clave|contrasena|password|pin)\b[^\d]{0,20}(\d{3,10})",
    re.IGNORECASE,
)


def redactar_datos_sensibles(texto: str) -> tuple[str, list[str]]:
    """Reemplaza tarjetas y claves por una marca antes de enviar el texto a cualquier destino.

    Devuelve `(texto_redactado, hallazgos)`. Los hallazgos son etiquetas
    genéricas ("tarjeta", "clave"), nunca el dato original.
    """
    hallazgos: list[str] = []

    def _tarjeta(_: re.Match[str]) -> str:
        hallazgos.append("tarjeta")
        return "[TARJETA REDACTADA]"

    def _clave(match: re.Match[str]) -> str:
        hallazgos.append("clave")
        return f"{match.group(1)} [CLAVE REDACTADA]"

    redactado = RE_TARJETA.sub(_tarjeta, texto)
    redactado = RE_CLAVE.sub(_clave, redactado)
    return redactado, sorted(set(hallazgos))


def tema_critico(texto: str, temas: Iterable[str]) -> bool:
    """True si el texto toca un tema sensible de la lista (pagos, envíos, reclamos…)."""
    normalizado = normalizar(texto)
    return any(normalizar(tema) in normalizado for tema in temas)


def validar_salida(texto: str) -> tuple[str, bool, str]:
    """Revisa la respuesta del LLM antes de entregarla.

    Devuelve `(texto_validado, bloqueado, motivo)`: una palabra fuera de política
    reemplaza toda la respuesta; un dato sensible solo se redacta.
    """
    normalizado = normalizar(texto)
    for palabra in PALABRAS_SALIDA:
        if normalizar(palabra) in normalizado:
            return respuesta_bloqueo(palabra), True, f"salida con palabra prohibida: {palabra}"

    redactado, hallazgos = redactar_datos_sensibles(texto)
    if hallazgos:
        return redactado, False, f"salida con datos sensibles redactados: {', '.join(hallazgos)}"
    return texto, False, ""
