"""Búsqueda tolerante a errores de tipeo, presupuesto y al menos tres opciones."""

from __future__ import annotations

from app.persistencia.catalogo import corregir
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from tests.datos_prueba import ISBN_CUENTOS, ISBN_DRAGON, ISBN_MAGO, ISBN_POEMAS, ISBN_SUMAS
from tests.dobles import ModeloFalso, llamada, respuesta

VOCABULARIO = {"dracula", "silmarillion", "secreta", "conciencia", "porter", "mago"}


def test_corrige_errores_de_tipeo_y_no_inventa() -> None:
    assert corregir(["drakula"], VOCABULARIO) == (["dracula"], {"drakula": "dracula"})
    assert corregir(["silmarilion"], VOCABULARIO)[1] == {"silmarilion": "silmarillion"}
    assert corregir(["receta"], VOCABULARIO) == (["receta"], {})  # otra primera letra
    assert corregir(["cocina"], VOCABULARIO) == (["cocina"], {})  # largo muy distinto
    assert corregir(["mag"], VOCABULARIO) == (["mag"], {})  # muy corta para corregir
    # "harry" no se parece a nada: corregir solo "poter" cambiaría el pedido por otro
    assert corregir(["harry", "poter"], VOCABULARIO) == (["harry", "poter"], {})


def test_la_busqueda_entiende_un_titulo_o_un_autor_mal_escritos(bd: BaseDatos) -> None:
    libros, correcciones = bd.buscar("la escuela de magoz")
    assert [lb["isbn"] for lb in libros] == [ISBN_MAGO] and correcciones == {"magoz": "mago"}
    libros, correcciones = bd.buscar("", autor="elena roka")
    assert [lb["isbn"] for lb in libros] == [ISBN_DRAGON]
    assert correcciones == {"elena roka": "elena roca"}
    assert bd.buscar("", autor="nadie conocido") == ([], {})


def test_si_las_correcciones_no_coinciden_en_un_libro_no_se_inventa(bd: BaseDatos) -> None:
    # "magoz" -> "mago" y "poemaz" -> "poema" existen, pero en libros distintos
    assert bd.buscar("magoz poemaz") == ([], {})


def test_el_presupuesto_filtra_la_busqueda(bd: BaseDatos) -> None:
    baratos = bd.buscar_libros("", precio_max=40, limite=10)
    assert {lb["isbn"] for lb in baratos} == {ISBN_SUMAS, ISBN_CUENTOS, ISBN_POEMAS}
    caros = bd.buscar_libros("", precio_min=60, limite=10)
    assert all(lb["precio"] >= 60 for lb in caros) and ISBN_MAGO in {lb["isbn"] for lb in caros}


def test_con_pocos_resultados_se_completa_con_relacionados(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    r = h.ejecutar("buscar_libros", {"consulta": "la escuela de magos"}, "a")
    assert r["libros"][0]["isbn"] == ISBN_MAGO
    relacionados = r.get("relacionados", [])
    assert ISBN_MAGO not in {lb["isbn"] for lb in relacionados}
    assert len(r["libros"]) + len(relacionados) <= 3
    pocos = h.ejecutar("buscar_libros", {"consulta": "cuentos de colores para aprender"}, "a")
    assert [lb["isbn"] for lb in pocos["libros"]] == [ISBN_CUENTOS]
    assert [lb["isbn"] for lb in pocos["relacionados"]] == [ISBN_SUMAS]
    por_isbn = h.ejecutar("buscar_libros", {"consulta": ISBN_MAGO}, "a")
    assert "relacionados" not in por_isbn  # un ISBN es un libro concreto
    tipeo = h.ejecutar("buscar_libros", {"consulta": "escuela de magoz"}, "a")
    assert tipeo["correcciones"] == {"magoz": "mago"}


def test_el_presupuesto_filtra_la_recomendacion(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    r = h.ejecutar("recomendar_libros", {"necesidad": "magia y dragones", "precio_max": 50}, "a")
    assert "recomendaciones" not in r or all(x["precio"] <= 50 for x in r["recomendaciones"])
    nada = h.ejecutar("recomendar_libros", {"necesidad": "magia", "precio_max": 1}, "a")
    assert "presupuesto" in nada["error"]


def test_los_relacionados_tambien_se_muestran_como_tarjetas(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("buscar_libros", consulta="la escuela de magos")]),
        respuesta("La tengo."),
    ]
    r = servicio_con_modelo.responder("¿tienen La escuela de magos?", "a", "s-b")
    resultado = servicio_con_modelo.herramientas.ejecutar(
        "buscar_libros", {"consulta": "la escuela de magos"}, "a"
    )
    esperados = [lb["isbn"] for lb in resultado["libros"] + resultado.get("relacionados", [])]
    assert [lb["isbn"] for lb in r.libros] == esperados


def test_los_relacionados_no_impiden_agregar_el_libro_pedido(bd: BaseDatos) -> None:
    from app.servicios.turno import Acumulado
    from app.servicios.verificacion import revisar_respuesta

    encontrado = {"isbn": ISBN_CUENTOS}
    acumulado = Acumulado(
        usadas=[{"nombre": "buscar_libros"}],
        libros=[encontrado, {"isbn": ISBN_SUMAS, "relacionado": True}],
    )
    problema = revisar_respuesta(
        "¿Lo agrego al carrito?", acumulado, bd, "ana", "quiero Cuentos de colores"
    )
    assert problema is not None and "actualizar_carrito" in problema.instruccion
