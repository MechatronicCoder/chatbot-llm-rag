"""Base de datos: transacciones, pagos, conversación y métricas."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.persistencia.db import BaseDatos, ErrorPedido


def test_inicializar_es_idempotente(bd: BaseDatos) -> None:
    antes = bd.estadisticas()["productos"]
    bd.inicializar()
    assert bd.estadisticas()["productos"] == antes == 20


def test_reserva_atomica_no_vende_lo_que_no_hay(bd: BaseDatos) -> None:
    producto = bd.obtener_producto("GPU001")
    assert producto is not None
    for _ in range(producto["stock"]):
        bd.reservar_pedido("alumno", "GPU001", 1)
    with pytest.raises(ErrorPedido):
        bd.reservar_pedido("alumno", "GPU001", 1)
    assert bd.obtener_producto("GPU001")["stock"] == 0  # type: ignore[index]


def test_reserva_rechaza_cantidad_o_producto_invalido(bd: BaseDatos) -> None:
    with pytest.raises(ErrorPedido):
        bd.reservar_pedido("alumno", "LAPTOP001", 0)
    with pytest.raises(ErrorPedido):
        bd.reservar_pedido("alumno", "NO-EXISTE", 1)


def test_pago_cambia_el_estado_y_registra_el_pago(bd: BaseDatos) -> None:
    pedido = bd.reservar_pedido("alumno", "LAPTOP001", 1)
    pago = bd.registrar_pago("alumno", pedido["pedido_id"], "transferencia")
    assert pago["estado"] == "pagado"
    assert bd.consultar_pedido(pedido["pedido_id"])["estado"] == "pagado"  # type: ignore[index]
    assert len(bd.pagos_de(pedido["pedido_id"])) == 1
    assert bd.ventas("alumno")[0]["pedido_id"] == pedido["pedido_id"]


def test_no_se_cobra_un_pedido_cancelado(bd: BaseDatos) -> None:
    pedido = bd.reservar_pedido("alumno", "LAPTOP001", 1)
    bd.cancelar_pedido(pedido["pedido_id"])
    with pytest.raises(ErrorPedido):
        bd.registrar_pago("alumno", pedido["pedido_id"], "tarjeta")


def test_ventas_solo_cuenta_pagados(bd: BaseDatos) -> None:
    a = bd.reservar_pedido("alumno", "LAPTOP001", 1)
    b = bd.reservar_pedido("alumno", "MONITOR001", 1)
    bd.registrar_pago("alumno", b["pedido_id"], "efectivo")
    ids = [v["pedido_id"] for v in bd.ventas()]
    assert b["pedido_id"] in ids
    assert a["pedido_id"] not in ids


def test_guardar_turno_y_leer_historial(bd: BaseDatos) -> None:
    bd.guardar_turno(
        "t-1", "s-1", "alumno", "hola", "buenas", ruta="directa", tokens=(10, 5), latencia_ms=120
    )
    mensajes = bd.mensajes_de("s-1")
    assert [m["rol"] for m in mensajes] == ["user", "assistant"]
    assert mensajes[1]["tokens_entrada"] == 10
    assert mensajes[1]["latencia_ms"] == 120
    assert bd.contar_turnos("s-1") == 1


def test_metricas_por_ruta(bd: BaseDatos) -> None:
    bd.guardar_turno("t-1", "s-1", "a", "hola", "buenas", ruta="faq", latencia_ms=100)
    bd.guardar_turno("t-2", "s-1", "a", "hola", "buenas", ruta="faq", latencia_ms=300)
    bd.guardar_turno("t-3", "s-1", "a", "hola", "buenas", ruta="accion", latencia_ms=200)
    metricas = bd.metricas()
    assert metricas["turnos"] == 3
    assert {f["ruta"]: f["turnos"] for f in metricas["por_ruta"]}["faq"] == 2
    assert metricas["latencia_p95"] >= metricas["latencia_p50"]


def test_borrar_sesion(bd: BaseDatos) -> None:
    bd.guardar_turno("t-1", "s-9", "a", "hola", "buenas")
    assert bd.borrar_sesion("s-9") == 2
    assert bd.mensajes_de("s-9") == []


def test_busqueda_por_palabras(bd: BaseDatos) -> None:
    assert bd.buscar_productos("laptops")[0]["categoria"] == "laptop"
    assert bd.buscar_productos("monitores")[0]["categoria"] == "monitor"
    assert bd.buscar_productos("tablets") == []


def test_filtro_por_categoria(bd: BaseDatos) -> None:
    productos = bd.buscar_productos("", categoria="Monitor")
    assert productos and all(p["categoria"] == "monitor" for p in productos)


def test_cancelar_pedido_inexistente_o_ya_cancelado(bd: BaseDatos) -> None:
    with pytest.raises(ErrorPedido, match="No existe"):
        bd.cancelar_pedido("PED-00000000")
    pedido = bd.reservar_pedido("alumno", "LAPTOP001", 1)
    bd.cancelar_pedido(pedido["pedido_id"])
    with pytest.raises(ErrorPedido, match="no se puede cancelar"):
        bd.cancelar_pedido(pedido["pedido_id"])


def test_catalogo_invalido_no_deja_la_base_a_medias(tmp_path: Path) -> None:
    catalogo = tmp_path / "catalogo.json"
    catalogo.write_text('{"productos": [{"codigo": "X"}]}', encoding="utf-8")
    base = BaseDatos(tmp_path / "t.db", catalogo)
    with pytest.raises(Exception):  # noqa: B017 - cualquier error de carga sirve
        base.inicializar()
    assert base.estadisticas()["productos"] == 0
