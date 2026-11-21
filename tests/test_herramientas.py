"""Herramientas (function calling) contra la base de datos."""

from __future__ import annotations

from app.servicios.chat import ServicioChat
from app.servicios.herramientas import ESQUEMAS


def test_buscar_productos_encuentra_plurales(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar("buscar_productos", {"consulta": "laptops"}, "alumno")
    assert r["encontrados"] >= 1
    assert all(p["categoria"] == "laptop" for p in r["productos"])


def test_consultar_stock_devuelve_unidades(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar("consultar_stock", {"codigo": "LAPTOP001"}, "alumno")
    assert r["codigo"] == "LAPTOP001"
    assert r["stock"] > 0
    assert r["precio"] > 0


def test_consultar_stock_de_codigo_inexistente(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar("consultar_stock", {"codigo": "NO-EXISTE"}, "alumno")
    assert "error" in r


def test_reservar_descuenta_stock(servicio: ServicioChat) -> None:
    antes = servicio.bd.obtener_producto("LAPTOP001")["stock"]  # type: ignore[index]
    r = servicio.herramientas.ejecutar(
        "reservar_pedido", {"codigo": "LAPTOP001", "cantidad": 2}, "alumno"
    )
    assert r["pedido_id"].startswith("PED-")
    assert r["estado"] == "reservado"
    assert servicio.bd.obtener_producto("LAPTOP001")["stock"] == antes - 2  # type: ignore[index]


def test_reservar_mas_de_lo_que_hay_falla_con_mensaje_claro(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar(
        "reservar_pedido", {"codigo": "LAPTOP001", "cantidad": 5000}, "alumno"
    )
    assert "quedan" in r["error"].lower()


def test_cobrar_marca_el_pedido_pagado(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    pedido = h.ejecutar("reservar_pedido", {"codigo": "LAPTOP001", "cantidad": 1}, "a")
    pago = h.ejecutar("cobrar_pedido", {"pedido_id": pedido["pedido_id"], "metodo": "tarjeta"}, "a")
    assert pago["estado"] == "pagado"
    assert pago["pago_id"].startswith("PAG-")


def test_no_se_cobra_dos_veces(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    pedido = h.ejecutar("reservar_pedido", {"codigo": "LAPTOP001", "cantidad": 1}, "a")
    argumentos = {"pedido_id": pedido["pedido_id"], "metodo": "tarjeta"}
    h.ejecutar("cobrar_pedido", argumentos, "a")
    segundo = h.ejecutar("cobrar_pedido", argumentos, "a")
    assert "pagado" in segundo["error"]


def test_no_se_cobra_ni_se_consulta_el_pedido_de_otro(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    pedido = h.ejecutar("reservar_pedido", {"codigo": "MONITOR001", "cantidad": 1}, "dueno")
    cobro = h.ejecutar(
        "cobrar_pedido", {"pedido_id": pedido["pedido_id"], "metodo": "tarjeta"}, "intruso"
    )
    assert "otro cliente" in cobro["error"]
    consulta = h.ejecutar("consultar_pedido", {"pedido_id": pedido["pedido_id"]}, "intruso")
    assert "error" in consulta
    assert "MONITOR" not in str(consulta)


def test_metodo_de_pago_invalido(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    pedido = h.ejecutar("reservar_pedido", {"codigo": "MONITOR001", "cantidad": 1}, "a")
    r = h.ejecutar("cobrar_pedido", {"pedido_id": pedido["pedido_id"], "metodo": "bitcoin"}, "a")
    assert "error" in r


def test_cancelar_devuelve_el_stock(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    antes = servicio.bd.obtener_producto("MOUSE001")["stock"]  # type: ignore[index]
    pedido = h.ejecutar("reservar_pedido", {"codigo": "MOUSE001", "cantidad": 3}, "a")
    h.ejecutar("cancelar_pedido", {"pedido_id": pedido["pedido_id"]}, "a")
    assert servicio.bd.obtener_producto("MOUSE001")["stock"] == antes  # type: ignore[index]


def test_buscar_politica_usa_el_rag(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar("buscar_politica", {"pregunta": "garantía"}, "a")
    assert r["hallazgos"]
    assert r["hallazgos"][0]["score"] > 0


def test_herramienta_desconocida_o_argumentos_malos_no_rompen(servicio: ServicioChat) -> None:
    assert "error" in servicio.herramientas.ejecutar("borrar_todo", {}, "a")
    r = servicio.herramientas.ejecutar("reservar_pedido", {"cantidad": "muchas"}, "a")
    assert "error" in r


def test_los_esquemas_declaran_las_siete_herramientas() -> None:
    assert {e["name"] for e in ESQUEMAS} == {
        "buscar_productos",
        "consultar_stock",
        "reservar_pedido",
        "consultar_pedido",
        "cancelar_pedido",
        "cobrar_pedido",
        "buscar_politica",
    }


def test_consultar_pedido_propio(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    pedido = h.ejecutar("reservar_pedido", {"codigo": "MOUSE001", "cantidad": 1}, "a")
    r = h.ejecutar("consultar_pedido", {"pedido_id": pedido["pedido_id"]}, "a")
    assert r["estado"] == "reservado"


def test_cancelar_inexistente_o_ajeno(servicio: ServicioChat) -> None:
    h = servicio.herramientas
    assert "No existe" in h.ejecutar("cancelar_pedido", {"pedido_id": "PED-NADA"}, "a")["error"]
    pedido = h.ejecutar("reservar_pedido", {"codigo": "MOUSE001", "cantidad": 1}, "dueno")
    ajeno = h.ejecutar("cancelar_pedido", {"pedido_id": pedido["pedido_id"]}, "intruso")
    assert "otro cliente" in ajeno["error"]


def test_argumentos_de_tipo_incorrecto(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar("reservar_pedido", {"codigo": "X", "cantidad": None}, "a")
    assert "Argumentos inválidos" in r["error"]


def test_politica_sin_resultados(servicio: ServicioChat) -> None:
    r = servicio.herramientas.ejecutar("buscar_politica", {"pregunta": "receta de pizza"}, "a")
    assert "No hay documentos" in r["error"]
