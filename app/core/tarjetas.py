"""Tarjetas de pago: validación y la parte que se puede guardar.

De una tarjeta solo se guarda la marca y los últimos cuatro dígitos ("Visa •••• 4242"),
como hacen las tiendas que cobran con una pasarela de pago: el número completo, la
fecha y el CVV nunca se guardan ni llegan al modelo. En la demo el cobro es simulado.
"""

from __future__ import annotations

import re

from app.core.isbn import isbn13_valido
from app.core.seguridad import RE_TARJETA

# Prefijos de las marcas más comunes (los primeros dígitos del número).
MARCAS = (
    ("American Express", re.compile(r"^3[47]")),
    ("Diners Club", re.compile(r"^3(?:0[0-5]|[68])")),
    ("Mastercard", re.compile(r"^(?:5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]|720))")),
    ("Visa", re.compile(r"^4")),
)


def solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def luhn_valido(numero: str) -> bool:
    """Dígito verificador de las tarjetas (algoritmo de Luhn), de 13 a 19 dígitos."""
    digitos = solo_digitos(numero)
    if not 13 <= len(digitos) <= 19:
        return False
    suma = 0
    for i, d in enumerate(reversed(digitos)):
        n = int(d) * (2 if i % 2 else 1)
        suma += n - 9 if n > 9 else n
    return suma % 10 == 0


def marca_de(numero: str) -> str:
    digitos = solo_digitos(numero)
    return next((nombre for nombre, patron in MARCAS if patron.match(digitos)), "Tarjeta")


def etiqueta(marca: str, ultimos4: str) -> str:
    return f"{marca} •••• {ultimos4}"


def tarjetas_en(texto: str) -> list[str]:
    """Números de tarjeta válidos que aparecen en un mensaje (un ISBN no cuenta)."""
    numeros = (solo_digitos(m.group(0)) for m in RE_TARJETA.finditer(texto))
    return [n for n in dict.fromkeys(numeros) if luhn_valido(n) and not isbn13_valido(n)]
