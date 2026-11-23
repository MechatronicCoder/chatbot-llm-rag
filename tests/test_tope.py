"""Tope de gasto: pasado el consumo de 24 horas, el turno sigue en modo demo."""

from __future__ import annotations

import pytest

from app.llm.cliente import SinProveedores
from app.llm.tope import AVISO_TOPE, ModeloConTope
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from tests.dobles import ModeloFalso, respuesta


class Cerrable(ModeloFalso):
    cerrado = False

    def cerrar(self) -> None:
        self.cerrado = True


def test_sin_tope_o_por_debajo_llama_al_modelo() -> None:
    modelo = ModeloFalso([respuesta("uno"), respuesta("dos")])
    assert ModeloConTope(modelo, lambda: 10**9, 0).chat([]).texto == "uno"
    assert ModeloConTope(modelo, lambda: 99, 100).chat([]).texto == "dos"


def test_al_llegar_al_tope_no_llama_y_avisa() -> None:
    modelo = ModeloFalso()
    con_tope = ModeloConTope(modelo, lambda: 100, 100)
    assert con_tope.agotado
    with pytest.raises(SinProveedores, match="límite diario"):
        con_tope.chat([])
    assert not modelo.llamadas


def test_delega_estado_nombre_y_cierre() -> None:
    cerrable = Cerrable()
    con_tope = ModeloConTope(cerrable, lambda: 0, 100)
    assert con_tope.configurado and con_tope.modelo_principal == "falso"
    con_tope.cerrar()
    assert cerrable.cerrado
    ModeloConTope(ModeloFalso(), lambda: 0, 100).cerrar()  # sin `cerrar`, no hace nada


def test_los_tokens_de_24_horas_salen_de_la_base(bd: BaseDatos) -> None:
    assert bd.tokens_ultimas_24h() == 0
    bd.guardar_turno("T-1", "s-1", "ana", "hola", "buenas", tokens=(100, 20))
    bd.guardar_turno("T-2", "s-1", "ana", "otro", "listo", tokens=(50, 5))
    assert bd.tokens_ultimas_24h() == 175
    with bd.conectar() as con:
        con.execute(
            "update mensajes set creado_en = '2020-01-01T00:00:00+00:00' where turno_id = 'T-1'"
        )
    assert bd.tokens_ultimas_24h() == 55
    assert bd.metricas()["tokens_24h"] == 55


def test_pasado_el_tope_el_turno_responde_en_modo_demo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    bd = servicio_con_modelo.bd
    servicio_con_modelo.llm = ModeloConTope(modelo, bd.tokens_ultimas_24h, 200)
    primero = servicio_con_modelo.responder("busco algo de terror", "ana", "s-t1")
    assert primero.modo == "real" and bd.tokens_ultimas_24h() == 120

    modelo.respuestas = [respuesta("no debería llegar")]
    bd.guardar_turno("T-x", "s-otra", "ana", "algo", "algo", tokens=(80, 0))
    segundo = servicio_con_modelo.responder("busco algo de misterio", "ana", "s-t1")
    assert segundo.modo == "demo" and segundo.aviso == AVISO_TOPE
    assert len(modelo.llamadas) == 1
