"""Catálogo de prueba, independiente de `datos/catalogo.json`.

El catálogo real se reemplaza por el de la librería; las pruebas no deben
romperse por eso. Los ISBN usan el prefijo 978-000-000 con dígito verificador
válido y los títulos son inventados.
"""

from __future__ import annotations

from typing import Any


def isbn_prueba(n: int) -> str:
    base = f"978000000{n:03d}"
    suma = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(base))
    return base + str((10 - suma % 10) % 10)


ISBN_SUMAS = isbn_prueba(1)
ISBN_CUENTOS = isbn_prueba(2)
ISBN_MAGO = isbn_prueba(3)
ISBN_MITOS = isbn_prueba(4)
ISBN_GOBLIN = isbn_prueba(5)
ISBN_DRAGON = isbn_prueba(6)
ISBN_DISTOPIA = isbn_prueba(7)
ISBN_AGOTADO = isbn_prueba(8)
ISBN_UN_VOTO = isbn_prueba(9)
ISBN_POEMAS = isbn_prueba(10)


def _libro(isbn: str, titulo: str, autor: str, categoria: str, **extra: Any) -> dict[str, Any]:
    return {"isbn": isbn, "titulo": titulo, "autor": autor, "categoria": categoria, **extra}


LIBROS_PRUEBA: list[dict[str, Any]] = [
    _libro(ISBN_SUMAS, "Sumas divertidas", "Ana Números", "educativo", genero="actividades",
           temas=["matemáticas", "sumar", "números"], tono="lúdico", edad_min=4, edad_max=6,
           sinopsis="Actividades con dibujos para aprender a sumar jugando.",
           valoracion=4.5, num_valoraciones=60, precio=29.9, stock=10, similares=[ISBN_CUENTOS]),
    _libro(ISBN_CUENTOS, "Cuentos de colores", "Luis Pinto", "infantil", genero="cuentos",
           temas=["emociones", "colores"], tono="tierno", edad_min=3, edad_max=6,
           sinopsis="Cuentos breves para hablar de las emociones con los más pequeños.",
           precio=34.9, stock=6),
    _libro(ISBN_MAGO, "La escuela de magos", "Rosa Varita", "juvenil", genero="fantasía",
           temas=["magia", "escuela", "amistad"], protagonista="un niño que descubre que es mago",
           tono="aventurero", edad_min=9, sinopsis="Un niño descubre que es mago y entra a una "
           "escuela de magia donde vive su primera aventura.",
           valoracion=4.9, num_valoraciones=900, precio=69.9, stock=8,
           similares=[ISBN_MITOS, ISBN_AGOTADO]),
    _libro(ISBN_MITOS, "El hijo del mar", "Pedro Olas", "juvenil", genero="fantasía",
           temas=["mitología", "dioses", "aventura"], protagonista="hijo de un dios del mar",
           tono="humorístico", edad_min=10, sinopsis="Un adolescente descubre que es hijo de un "
           "dios y viaja para evitar una guerra.",
           valoracion=4.7, num_valoraciones=500, precio=64.9, stock=5, similares=[ISBN_MAGO]),
    _libro(ISBN_GOBLIN, "El goblin miope", "Jorge Cueva", "novela", genero="fantasía",
           temas=["goblins", "humor", "aventura"], protagonista="un goblin pequeño y miope",
           tono="humorístico", edad_min=14, idioma="inglés",
           sinopsis="Un goblin débil es arrastrado por aventureros a buscar un tesoro.",
           valoracion=4.2, num_valoraciones=90, precio=59.9, stock=3, similares=[ISBN_DRAGON]),
    _libro(ISBN_DRAGON, "La montaña del dragón", "Elena Roca", "novela", genero="fantasía",
           temas=["dragones", "montaña", "tesoro", "viaje"], tono="épico", edad_min=10,
           sinopsis="Una compañía viaja a la montaña del dragón para recuperar un tesoro.",
           valoracion=4.8, num_valoraciones=600, precio=64.9, stock=7,
           similares=[ISBN_GOBLIN, ISBN_UN_VOTO]),
    _libro(ISBN_DISTOPIA, "La ciudad vigilada", "Marta Gris", "novela",
           genero="ciencia ficción", temas=["distopía", "vigilancia", "rebelión"], tono="oscuro",
           edad_min=15, sinopsis="En una ciudad vigilada por cámaras, una mujer empieza a "
           "rebelarse.", valoracion=4.6, num_valoraciones=300, precio=49.9, stock=9),
    _libro(ISBN_AGOTADO, "El ladrón del tiempo", "Clara Reloj", "juvenil", genero="fantasía",
           temas=["tiempo", "magia"], edad_min=10, sinopsis="Unos hombres grises roban el tiempo "
           "de la ciudad.", valoracion=4.6, num_valoraciones=140, precio=54.9, stock=0),
    _libro(ISBN_UN_VOTO, "Dragones de la montaña", "Iván Nuevo", "novela", genero="fantasía",
           temas=["dragones", "montaña", "tesoro", "viaje"], tono="épico", edad_min=10,
           sinopsis="Una compañía viaja a la montaña de los dragones para recuperar un tesoro.",
           valoracion=5.0, num_valoraciones=1, precio=44.9, stock=4),
    _libro(ISBN_POEMAS, "Poemas del puerto", "Raúl Muelle", "poesia", genero="poesía",
           temas=["mar", "nostalgia"], edad_min=16, sinopsis="Poemas sobre el puerto y el mar.",
           valoracion=4.1, num_valoraciones=20, precio=39.9, stock=3, formato="tapa dura"),
]  # fmt: skip
