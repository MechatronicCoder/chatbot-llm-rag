"""Herramientas (function calling): catálogo, recomendación, fichas, pedidos y políticas."""

from __future__ import annotations

from app.servicios.chat import ServicioChat
from app.servicios.herramientas import ESQUEMAS, Herramientas
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


def carrito(h: Herramientas, usuario: str, *items: tuple[str, int], zona: str | None = "lima"):  # type: ignore[no-untyped-def]
    cambios = [{"isbn": isbn, "cantidad": cantidad} for isbn, cantidad in items]
    return h.ejecutar("actualizar_carrito", {"cambios": cambios, "zona": zona}, usuario)


def test_reservar_descuenta_stock(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    c = carrito(h, "a", (ISBN_MAGO, 2))
    assert c["estado"] == "carrito" and "Total: S/" in c["resumen"]
    assert servicio.bd.obtener_libro(ISBN_MAGO)["stock"] == 8  # type: ignore[index]
    r = h.ejecutar("reservar_pedido", {}, "a")
    assert r["pedido_id"] == c["pedido_id"] and r["estado"] == "reservado"
    assert r["lineas"][0]["titulo"] == "La escuela de magos" and "reservado" in r["resumen"]
    assert servicio.bd.obtener_libro(ISBN_MAGO)["stock"] == 6  # type: ignore[index]
    assert "Solo quedan 3" in carrito(h, "a", (ISBN_POEMAS, 5))["error"]
    assert "de 0 a 50" in carrito(h, "a", (ISBN_POEMAS, 51))["error"]


def test_el_bot_no_puede_cobrar_y_el_pedido_se_consulta(servicio: ServicioChat) -> None:
    """Demo: con "con transferencia bancaria" el bot cobraba solo. Ahora paga el cliente."""
    h = servicio.herramientas
    carrito(h, "dueno", (ISBN_DRAGON, 1))
    pedido = h.ejecutar("reservar_pedido", {}, "dueno")
    argumentos = {"pedido_id": pedido["pedido_id"], "metodo": "transferencia"}
    assert "desconocida" in h.ejecutar("cobrar_pedido", argumentos, "dueno")["error"]
    assert servicio.bd.consultar_pedido(pedido["pedido_id"])["estado"] == "reservado"  # type: ignore[index]
    consulta_ajena = h.ejecutar("consultar_pedido", {"pedido_id": pedido["pedido_id"]}, "intruso")
    assert "error" in consulta_ajena and "dragón" not in str(consulta_ajena)
    propia = h.ejecutar("consultar_pedido", {"pedido_id": pedido["pedido_id"]}, "dueno")
    assert propia["estado"] == "reservado" and propia["resumen"]


def test_sin_zona_no_se_reserva(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    sin_zona = carrito(h, "a", (ISBN_SUMAS, 1), zona=None)
    assert "falta elegir la zona" in sin_zona["resumen"]
    assert "Falta la zona" in h.ejecutar("reservar_pedido", {}, "a")["error"]
    carrito(h, "a", zona="provincias")
    assert h.ejecutar("reservar_pedido", {}, "a")["estado"] == "reservado"
    assert servicio.bd.obtener_libro(ISBN_SUMAS)["stock"] == 9  # type: ignore[index]


def test_metodo_invalido_y_cancelaciones(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    carrito(h, "a", (ISBN_SUMAS, 3))
    pedido = h.ejecutar("reservar_pedido", {}, "a")["pedido_id"]
    assert "otro cliente" in h.ejecutar("cancelar_pedido", {"pedido_id": pedido}, "b")["error"]
    cancelado = h.ejecutar("cancelar_pedido", {"pedido_id": pedido}, "a")
    assert cancelado["estado"] == "cancelado" and cancelado["unidades_devueltas"] == 3
    assert servicio.bd.obtener_libro(ISBN_SUMAS)["stock"] == 10  # type: ignore[index]
    assert "No existe" in h.ejecutar("cancelar_pedido", {"pedido_id": "PED-NADA"}, "a")["error"]
    abierto = carrito(h, "a", (ISBN_MAGO, 1))["pedido_id"]
    vaciado = h.ejecutar("cancelar_pedido", {"pedido_id": abierto}, "a")
    assert vaciado["estado"] == "cancelado" and vaciado["unidades_devueltas"] == 0


def test_buscar_politica_usa_el_rag(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    r = h.ejecutar("buscar_politica", {"pregunta": "¿puedo cambiar un libro?"}, "a")
    assert r["hallazgos"][0]["id"] == "doc-cambios"
    assert "error" in h.ejecutar("buscar_politica", {"pregunta": "receta de pizza"}, "a")


def test_errores_de_uso_no_rompen(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    assert "desconocida" in h.ejecutar("borrar_todo", {}, "a")["error"]
    sin_cantidad = carrito(h, "a", (ISBN_SUMAS, None))  # type: ignore[arg-type]
    assert "Argumentos inválidos" in sin_cantidad["error"]
    assert "Zona de envío desconocida" in carrito(h, "a", zona="marte")["error"]
    assert "No hay un carrito abierto" in h.ejecutar("reservar_pedido", {}, "a")["error"]
    edad_rara = h.ejecutar("buscar_libros", {"consulta": "x", "edad": "muchos"}, "a")
    assert "Argumentos inválidos" in edad_rara["error"]


def test_error_inesperado_de_una_herramienta(servicio: ServicioChat, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def explota(*_: object, **__: object) -> None:
        raise RuntimeError("base caída")

    monkeypatch.setattr(servicio.bd, "buscar", explota)
    r = servicio.herramientas.ejecutar("buscar_libros", {"consulta": "x"}, "a")
    assert r == {"error": "RuntimeError: base caída"}


def test_los_esquemas_declaran_las_nueve_herramientas(servicio: ServicioChat) -> None:
    carro = next(e for e in ESQUEMAS if e["name"] == "actualizar_carrito")
    zonas = carro["parameters"]["properties"]["zona"]["enum"]
    assert set(zonas) == set(servicio.bd.tienda.envio)  # el esquema y tienda.json coinciden
    assert {e["name"] for e in ESQUEMAS} == {
        "buscar_libros",
        "recomendar_libros",
        "ficha_libro",
        "actualizar_carrito",
        "reservar_pedido",
        "consultar_pedido",
        "cancelar_pedido",
        "buscar_politica",
        "derivar_a_persona",
    }
