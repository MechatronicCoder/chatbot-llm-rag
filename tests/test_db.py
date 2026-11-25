"""Base de datos: sincronización del catálogo, versión del esquema, pedidos y métricas."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.catalogo.carga import ErrorCatalogo
from app.core.config import Settings
from app.persistencia.db import BaseDatos, ErrorPedido
from app.persistencia.esquema import ESQUEMA_VERSION
from tests.datos_prueba import (
    ISBN_AGOTADO,
    ISBN_CUENTOS,
    ISBN_DRAGON,
    ISBN_MAGO,
    ISBN_POEMAS,
    ISBN_SUMAS,
    LIBROS_PRUEBA,
    isbn_prueba,
)

NUEVO = isbn_prueba(11)


def reescribir_catalogo(ajustes: Settings, libros: list[dict[str, object]]) -> None:
    ruta = ajustes.carpeta_datos / "catalogo.json"
    ruta.write_text(json.dumps({"libros": libros}, ensure_ascii=False), encoding="utf-8")


def test_inicializar_es_idempotente(bd: BaseDatos) -> None:
    antes = bd.estadisticas()
    bd.inicializar()
    assert bd.estadisticas() == antes
    assert antes["libros"] == len(LIBROS_PRUEBA)
    assert bd.ultima_sincronizacion == {"nuevos": 0, "actualizados": 10, "retirados": 0}


def test_sincroniza_nuevos_cambios_y_retirados(ajustes: Settings, bd: BaseDatos) -> None:
    bd.reservar_pedido("alumno", ISBN_MAGO, 2)  # stock 8 -> 6
    libros = [dict(lb) for lb in LIBROS_PRUEBA if lb["isbn"] != ISBN_POEMAS]
    for lb in libros:
        if lb["isbn"] == ISBN_MAGO:
            lb["precio"] = 75.0
    nuevo = {"isbn": NUEVO, "titulo": "Nuevo", "autor": "Z", "categoria": "novela",
             "precio": 20, "stock": 4}  # fmt: skip
    reescribir_catalogo(ajustes, [*libros, nuevo])

    bd.inicializar()
    assert bd.ultima_sincronizacion == {"nuevos": 1, "actualizados": 9, "retirados": 1}
    mago = bd.obtener_libro(ISBN_MAGO)
    assert mago is not None and mago["precio"] == 75.0
    assert mago["stock"] == 6  # el stock de la base se conserva
    assert bd.obtener_libro(ISBN_POEMAS) is None  # retirado: ya no se ofrece
    assert bd.obtener_libro(NUEVO) is not None

    bd.inicializar(recargar_catalogo=True)
    assert bd.obtener_libro(ISBN_MAGO)["stock"] == 8  # type: ignore[index]


def test_un_libro_retirado_conserva_sus_pedidos(ajustes: Settings, bd: BaseDatos) -> None:
    pedido = bd.reservar_pedido("alumno", ISBN_POEMAS, 1)
    reescribir_catalogo(ajustes, [lb for lb in LIBROS_PRUEBA if lb["isbn"] != ISBN_POEMAS])
    bd.inicializar()
    consultado = bd.consultar_pedido(pedido["pedido_id"])
    assert consultado is not None and consultado["lineas"][0]["titulo"] == "Poemas del puerto"


def test_un_catalogo_invalido_no_toca_la_base(ajustes: Settings, bd: BaseDatos) -> None:
    reescribir_catalogo(ajustes, [{"isbn": "malo"}])
    with pytest.raises(ErrorCatalogo):
        bd.inicializar()
    assert bd.estadisticas()["libros"] == len(LIBROS_PRUEBA)


def test_una_base_con_otra_version_se_reconstruye(ajustes: Settings) -> None:
    ajustes.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ajustes.db_path) as con:  # esquema anterior (otra tienda)
        con.execute("create table productos (codigo text primary key, nombre text)")
        con.execute("create table pedidos (id text, codigo text)")
    base = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    base.inicializar()
    with sqlite3.connect(ajustes.db_path) as con:
        assert con.execute("pragma user_version").fetchone()[0] == ESQUEMA_VERSION
        tablas = {f[0] for f in con.execute("select name from sqlite_master where type='table'")}
    assert "productos" not in tablas
    assert base.reservar_pedido("alumno", ISBN_SUMAS, 1)["lineas"][0]["isbn"] == ISBN_SUMAS


def test_reserva_atomica_no_vende_lo_que_no_hay(bd: BaseDatos) -> None:
    for _ in range(3):
        bd.reservar_pedido("alumno", ISBN_POEMAS, 1)
    with pytest.raises(ErrorPedido, match="Solo quedan 0 ejemplares"):
        bd.reservar_pedido("alumno", ISBN_POEMAS, 1)
    with pytest.raises(ErrorPedido):
        bd.reservar_pedido("alumno", ISBN_AGOTADO, 1)


def test_reserva_rechaza_cantidad_o_isbn_invalido(bd: BaseDatos) -> None:
    with pytest.raises(ErrorPedido, match="mayor que cero"):
        bd.reservar_pedido("alumno", ISBN_SUMAS, 0)
    with pytest.raises(ErrorPedido, match="No hay un libro"):
        bd.reservar_pedido("alumno", "9780000000999", 1)


def test_reserva_acepta_isbn_con_guiones(bd: BaseDatos) -> None:
    pedido = bd.reservar_pedido("alumno", f"{ISBN_SUMAS[:3]}-{ISBN_SUMAS[3:]}", 2)
    assert pedido["lineas"][0]["isbn"] == ISBN_SUMAS
    assert (pedido["subtotal"], pedido["envio"], pedido["total"]) == (59.8, 10.0, 69.8)
    assert bd.obtener_libro(ISBN_SUMAS)["stock"] == 8  # type: ignore[index]


def test_pago_y_ventas(bd: BaseDatos) -> None:
    pedido = bd.reservar_pedido("alumno", ISBN_DRAGON, 1)
    pago = bd.registrar_pago("alumno", pedido["pedido_id"], "transferencia")
    assert pago["estado"] == "pagado"
    assert len(bd.pagos_de(pedido["pedido_id"])) == 1
    venta = bd.ventas("alumno")[0]
    assert venta["pedido_id"] == pedido["pedido_id"] and venta["titulo"] == "La montaña del dragón"
    otro = bd.reservar_pedido("alumno", ISBN_CUENTOS, 1)
    assert otro["pedido_id"] not in [v["pedido_id"] for v in bd.ventas()]


def test_cancelaciones_y_cobros_invalidos(bd: BaseDatos) -> None:
    with pytest.raises(ErrorPedido, match="No existe"):
        bd.cancelar_pedido("PED-00000000")
    pedido = bd.reservar_pedido("alumno", ISBN_SUMAS, 1)
    bd.cancelar_pedido(pedido["pedido_id"])
    assert bd.obtener_libro(ISBN_SUMAS)["stock"] == 10  # type: ignore[index]
    with pytest.raises(ErrorPedido, match="no se puede cancelar"):
        bd.cancelar_pedido(pedido["pedido_id"])
    with pytest.raises(ErrorPedido, match="no se puede cobrar"):
        bd.registrar_pago("alumno", pedido["pedido_id"], "transferencia")


def test_pedidos_del_cliente_y_categorias(bd: BaseDatos) -> None:
    bd.reservar_pedido("ana", ISBN_SUMAS, 1)
    bd.reservar_pedido("ana", ISBN_MAGO, 1)
    assert {p["lineas"][0]["isbn"] for p in bd.pedidos_de("ana")} == {ISBN_SUMAS, ISBN_MAGO}
    categorias = {c["categoria"]: c["libros"] for c in bd.categorias()}
    assert categorias["novela"] == 4 and categorias["juvenil"] == 3


def test_conversacion_y_metricas(bd: BaseDatos) -> None:
    bd.guardar_turno("t-1", "s-1", "a", "hola", "buenas", ruta="faq", latencia_ms=100,
                     tokens=(10, 5))  # fmt: skip
    bd.guardar_turno("t-2", "s-1", "a", "hola", "buenas", ruta="faq", latencia_ms=300)
    bd.guardar_turno("t-3", "s-1", "a", "hola", "buenas", ruta="recomendacion", latencia_ms=200)
    assert [m["rol"] for m in bd.mensajes_de("s-1")][:2] == ["user", "assistant"]
    assert bd.contar_turnos("s-1") == 3
    metricas = bd.metricas()
    assert metricas["turnos"] == 3
    assert {f["ruta"]: f["turnos"] for f in metricas["por_ruta"]}["faq"] == 2
    assert bd.borrar_sesion("s-1") == 6


def test_base_desde_cero(tmp_path: Path) -> None:
    from tests.conftest import crear_ajustes

    ajustes = crear_ajustes(tmp_path)
    base = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    assert base.inicializar()["libros"] == len(LIBROS_PRUEBA)


def test_un_error_al_sincronizar_revierte_todo(
    bd: BaseDatos, monkeypatch: pytest.MonkeyPatch
) -> None:
    def falla(*_: object, **__: object) -> dict[str, int]:
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(BaseDatos, "_sincronizar", staticmethod(falla))
    with pytest.raises(RuntimeError):
        bd.inicializar()
    assert bd.estadisticas()["libros"] == len(LIBROS_PRUEBA)
