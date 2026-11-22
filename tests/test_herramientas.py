"""Herramientas (function calling): catálogo, recomendación, fichas, pedidos y políticas."""

from __future__ import annotations

from app.servicios.chat import ServicioChat
from app.servicios.herramientas import ESQUEMAS
from tests.datos_prueba import (
    ISBN_AGOTADO,
    ISBN_CUENTOS,
    ISBN_DRAGON,
    ISBN_GOBLIN,
    ISBN_MAGO,
    ISBN_MITOS,
    ISBN_POEMAS,
    ISBN_SUMAS,
)


def test_buscar_libros_por_titulo_autor_e_isbn(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    por_titulo = h.ejecutar("buscar_libros", {"consulta": "escuela de magos"}, "a")
    assert por_titulo["libros"][0]["isbn"] == ISBN_MAGO
    por_autor = h.ejecutar("buscar_libros", {"consulta": "", "autor": "elena roca"}, "a")
    assert [lb["isbn"] for lb in por_autor["libros"]] == [ISBN_DRAGON]
    por_isbn = h.ejecutar("buscar_libros", {"consulta": f"isbn {ISBN_GOBLIN}"}, "a")
    assert por_isbn["encontrados"] == 1 and por_isbn["libros"][0]["isbn"] == ISBN_GOBLIN


def test_buscar_libros_filtra_y_ordena(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    ninos = h.ejecutar("buscar_libros", {"consulta": "", "edad": 5, "limite": 10}, "a")
    assert {lb["isbn"] for lb in ninos["libros"]} == {ISBN_SUMAS, ISBN_CUENTOS}
    novelas = h.ejecutar("buscar_libros", {"consulta": "", "categoria": "novela"}, "a")
    valoraciones = [lb["valoracion"] for lb in novelas["libros"]]
    assert valoraciones == sorted(valoraciones, reverse=True)
    assert h.ejecutar("buscar_libros", {"consulta": "tablets"}, "a")["encontrados"] == 0


def test_recomendar_libros(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    r = h.ejecutar("recomendar_libros", {"necesidad": "aprender a sumar", "edad": "5"}, "a")
    assert r["recomendaciones"][0]["isbn"] == ISBN_SUMAS
    assert "puntaje" in r["recomendaciones"][0]
    assert "error" in h.ejecutar("recomendar_libros", {"necesidad": "  "}, "a")
    sin_nada = h.ejecutar("recomendar_libros", {"necesidad": "recetas de cocina"}, "a")
    assert "no inventar" in sin_nada["error"]


def test_ficha_con_similares_disponibles(servicio: ServicioChat) -> None:
    ficha = servicio.herramientas.ejecutar("ficha_libro", {"isbn": ISBN_MAGO}, "a")
    assert ficha["titulo"] == "La escuela de magos"
    assert ficha["sinopsis"]
    similares = [s["isbn"] for s in ficha["similares_disponibles"]]
    assert similares == [ISBN_MITOS]  # el agotado no se sugiere
    assert ISBN_AGOTADO in ficha["similares"]
    assert "error" in servicio.herramientas.ejecutar("ficha_libro", {"isbn": "9780000000999"}, "a")


def test_reservar_descuenta_stock(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar("reservar_pedido", {"isbn": ISBN_MAGO, "cantidad": 2}, "a")
    assert r["pedido_id"].startswith("PED-") and r["titulo"] == "La escuela de magos"
    assert servicio.bd.obtener_libro(ISBN_MAGO)["stock"] == 6  # type: ignore[index]
    falta = servicio.herramientas.ejecutar(
        "reservar_pedido", {"isbn": ISBN_POEMAS, "cantidad": 50}, "a"
    )
    assert "Solo quedan 3" in falta["error"]


def test_cobrar_una_vez_y_solo_lo_propio(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    pedido = h.ejecutar("reservar_pedido", {"isbn": ISBN_DRAGON, "cantidad": 1}, "dueno")
    argumentos = {"pedido_id": pedido["pedido_id"], "metodo": "tarjeta"}
    assert "otro cliente" in h.ejecutar("cobrar_pedido", argumentos, "intruso")["error"]
    assert h.ejecutar("cobrar_pedido", argumentos, "dueno")["estado"] == "pagado"
    assert "pagado" in h.ejecutar("cobrar_pedido", argumentos, "dueno")["error"]
    consulta_ajena = h.ejecutar("consultar_pedido", {"pedido_id": pedido["pedido_id"]}, "intruso")
    assert "error" in consulta_ajena and "dragón" not in str(consulta_ajena)
    propia = h.ejecutar("consultar_pedido", {"pedido_id": pedido["pedido_id"]}, "dueno")
    assert propia["estado"] == "pagado"


def test_metodo_invalido_y_cancelaciones(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    pedido = h.ejecutar("reservar_pedido", {"isbn": ISBN_SUMAS, "cantidad": 3}, "a")["pedido_id"]
    assert "error" in h.ejecutar("cobrar_pedido", {"pedido_id": pedido, "metodo": "cripto"}, "a")
    assert "otro cliente" in h.ejecutar("cancelar_pedido", {"pedido_id": pedido}, "b")["error"]
    assert h.ejecutar("cancelar_pedido", {"pedido_id": pedido}, "a")["estado"] == "cancelado"
    assert servicio.bd.obtener_libro(ISBN_SUMAS)["stock"] == 10  # type: ignore[index]
    assert "No existe" in h.ejecutar("cancelar_pedido", {"pedido_id": "PED-NADA"}, "a")["error"]


def test_buscar_politica_usa_el_rag(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    r = h.ejecutar("buscar_politica", {"pregunta": "¿puedo cambiar un libro?"}, "a")
    assert r["hallazgos"][0]["id"] == "doc-cambios"
    assert "error" in h.ejecutar("buscar_politica", {"pregunta": "receta de pizza"}, "a")


def test_errores_de_uso_no_rompen(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    assert "desconocida" in h.ejecutar("borrar_todo", {}, "a")["error"]
    sin_cantidad = h.ejecutar("reservar_pedido", {"isbn": ISBN_SUMAS, "cantidad": None}, "a")
    assert "Argumentos inválidos" in sin_cantidad["error"]
    edad_rara = h.ejecutar("buscar_libros", {"consulta": "x", "edad": "muchos"}, "a")
    assert "Argumentos inválidos" in edad_rara["error"]


def test_error_inesperado_de_una_herramienta(servicio: ServicioChat, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def explota(*_: object, **__: object) -> None:
        raise RuntimeError("base caída")

    monkeypatch.setattr(servicio.bd, "buscar_libros", explota)
    r = servicio.herramientas.ejecutar("buscar_libros", {"consulta": "x"}, "a")
    assert r == {"error": "RuntimeError: base caída"}


def test_los_esquemas_declaran_las_ocho_herramientas() -> None:
    assert {e["name"] for e in ESQUEMAS} == {
        "buscar_libros",
        "recomendar_libros",
        "ficha_libro",
        "reservar_pedido",
        "consultar_pedido",
        "cancelar_pedido",
        "cobrar_pedido",
        "buscar_politica",
    }
