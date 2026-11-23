"""Hash y verificación de claves con PBKDF2-SHA256 (solo biblioteca estándar).

Formato del hash: ``pbkdf2_sha256$<iteraciones>$<sal en base64>$<derivada en base64>``.
La clave nunca se guarda: solo este hash, que se genera con
``scripts/hash_clave.py`` y se configura como ``ACCESO_CLAVE_HASH``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

ALGORITMO = "pbkdf2_sha256"
# Recomendación de OWASP para PBKDF2-HMAC-SHA256: hace caro cada intento de adivinar.
ITERACIONES = 600_000
RE_HASH = re.compile(
    r"^pbkdf2_sha256\$(?P<iteraciones>\d{4,7})\$(?P<sal>[A-Za-z0-9+/]+={0,2})"
    r"\$(?P<derivada>[A-Za-z0-9+/]+={0,2})$"
)


def _derivar(clave: str, sal: bytes, iteraciones: int, largo: int = 32) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal, iteraciones, dklen=largo)


def generar_hash(clave: str, *, iteraciones: int = ITERACIONES, sal: bytes | None = None) -> str:
    """Hash nuevo con sal aleatoria de 16 bytes."""
    sal = sal if sal is not None else secrets.token_bytes(16)
    derivada = _derivar(clave, sal, iteraciones)
    sal_b64 = base64.b64encode(sal).decode("ascii")
    derivada_b64 = base64.b64encode(derivada).decode("ascii")
    return f"{ALGORITMO}${iteraciones}${sal_b64}${derivada_b64}"


def hash_valido(valor: str) -> bool:
    """El texto tiene el formato de un hash generado por `generar_hash`."""
    return _partes(valor) is not None


def _partes(valor: str) -> tuple[int, bytes, bytes] | None:
    m = RE_HASH.match(valor)
    if not m:
        return None
    try:
        sal = base64.b64decode(m["sal"], validate=True)
        derivada = base64.b64decode(m["derivada"], validate=True)
    except ValueError:  # binascii.Error: relleno o largo inválido
        return None
    if len(sal) < 8 or len(derivada) < 16:
        return None
    return int(m["iteraciones"]), sal, derivada


def verificar_clave(clave: str, guardado: str) -> bool:
    """Compara en tiempo constante; un hash mal formado nunca valida."""
    partes = _partes(guardado)
    if partes is None:
        return False
    iteraciones, sal, esperada = partes
    return hmac.compare_digest(_derivar(clave, sal, iteraciones, len(esperada)), esperada)
