"""Conversaciones largas: resumen encadenado y preguntas de seguimiento."""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.contexto import mensajes_iniciales
from app.servicios.resumen import DATOS, Resumidor, datos_exactos
from app.servicios.turno import es_seguimiento
from tests.datos_prueba import ISBN_MAGO
from tests.dobles import ModeloFalso, respuesta


def conversar(bd: BaseDatos, sesion: str, turnos: int, desde: int = 0) -> None:
    for i in range(desde, desde + turnos):
        bd.guardar_turno(f"t-{sesion}-{i}", sesion, "ana", f"pregunta {i}", f"respuesta {i}")


def resumidor(bd: BaseDatos, registro: RegistroEventos, modelo: ModeloFalso) -> Resumidor:
    return Resumidor(bd, modelo, registro, ventana=4, en_segundo_plano=False)


def test_los_datos_exactos_se_copian_sin_el_modelo() -> None:
    texto = f"Quiere ISBN {ISBN_MAGO}; pedido ped-1a2b3c4d y pago PAG-99887766. PED-1A2B3C4D"
    assert datos_exactos(texto) == [ISBN_MAGO, "PED-1A2B3C4D", "PAG-99887766"]


def test_resume_lo_que_sale_de_la_ventana_y_encadena(
    bd: BaseDatos, registro: RegistroEventos
) -> None:
    modelo = ModeloFalso([respuesta("Busca fantasía para su hija."), respuesta("Sigue igual.")])
    r = resumidor(bd, registro, modelo)
    conversar(bd, "s1", 3)  # 6 mensajes: con ventana 4 salen 2, no alcanza
    assert not r.actualizar("s1") and not modelo.llamadas
    conversar(bd, "s1", 3, desde=3)  # 12 mensajes: salen 8
    bd.guardar_turno("t-s1-x", "s1", "ana", f"quiero {ISBN_MAGO}", "Reservado: PED-1A2B3C4D")
    assert r.actualizar("s1")
    guardado = bd.resumen_de("s1")
    assert guardado is not None and guardado["cubiertos"] == 10
    assert guardado["texto"].startswith("Busca fantasía")
    assert bd.tokens_ultimas_24h() == 120  # los tokens del resumen cuentan para el tope
    assert not r.actualizar("s1")  # nada nuevo fuera de la ventana

    conversar(bd, "s1", 4, desde=10)
    assert r.actualizar("s1")
    pedido = modelo.llamadas[-1]["mensajes"][-1]["content"]
    assert pedido.startswith("Resumen anterior:\nBusca fantasía") and DATOS not in pedido
    texto = bd.resumen_de("s1")["texto"]  # type: ignore[index]
    assert texto.endswith(f"{DATOS} {ISBN_MAGO}, PED-1A2B3C4D")  # se conservan los datos


def test_el_modelo_no_escribe_los_datos_exactos(bd: BaseDatos, registro: RegistroEventos) -> None:
    modelo = ModeloFalso([respuesta(f"Resumen.\n{DATOS} PED-INVENTADO")])
    conversar(bd, "s2", 6)
    assert resumidor(bd, registro, modelo).actualizar("s2")
    assert bd.resumen_de("s2")["texto"] == "Resumen."  # type: ignore[index]


def test_si_el_modelo_falla_queda_el_resumen_anterior(
    bd: BaseDatos, registro: RegistroEventos
) -> None:
    conversar(bd, "s3", 6)
    assert not resumidor(bd, registro, ModeloFalso(fallar=True)).actualizar("s3")
    assert bd.resumen_de("s3") is None
    assert any(e["evento"] == "resumen.fallo" for e in registro.leer(nivel="WARN"))


def test_programar_no_resume_sin_modelo_ni_dos_veces(
    bd: BaseDatos, registro: RegistroEventos
) -> None:
    conversar(bd, "s4", 6)
    sin_modelo = ModeloFalso(fallar=True)
    resumidor(bd, registro, sin_modelo).programar("s4")
    assert not sin_modelo.llamadas
    modelo = ModeloFalso()
    r = resumidor(bd, registro, modelo)
    r._en_curso.add("s4")  # ya hay uno en marcha
    r.programar("s4")
    assert not modelo.llamadas


def test_en_segundo_plano_el_resumen_llega_despues(
    bd: BaseDatos, registro: RegistroEventos
) -> None:
    conversar(bd, "s5", 6)
    Resumidor(bd, ModeloFalso(), registro, ventana=4).programar("s5")
    for _ in range(100):
        if bd.resumen_de("s5"):
            break
        time.sleep(0.02)
    assert bd.resumen_de("s5") is not None


def test_el_resumen_va_al_prompt_y_se_borra_con_la_sesion(
    servicio_con_modelo: ServicioChat,
) -> None:
    bd = servicio_con_modelo.bd
    conversar(bd, "s6", 1)
    bd.guardar_resumen("s6", "Busca terror para su sobrino de 13.", 2, tokens=10)
    sistema = mensajes_iniciales(
        "Prompt.", servicio_con_modelo.faq, bd, "¿y algo más?", "s6", saludo=False, limite=8
    )[0]["content"]
    assert "Busca terror para su sobrino de 13." in sistema
    bd.borrar_sesion("s6")
    assert bd.resumen_de("s6") is None


def test_un_seguimiento_busca_las_faq_con_el_mensaje_anterior(
    servicio_con_modelo: ServicioChat, monkeypatch: pytest.MonkeyPatch
) -> None:
    consultas: list[str] = []
    monkeypatch.setattr(
        servicio_con_modelo.faq, "contexto", lambda texto, *_: consultas.append(texto) or ""
    )
    bd = servicio_con_modelo.bd
    bd.guardar_turno("t-1", "s7", "ana", "¿cuánto cuesta el envío a Lima?", "S/ 10.")
    for mensaje in ("¿y a provincias?", "quiero otro libro"):
        mensajes_iniciales("P", servicio_con_modelo.faq, bd, mensaje, "s7", saludo=False, limite=8)
    assert consultas == ["¿cuánto cuesta el envío a Lima? ¿y a provincias?", "quiero otro libro"]


def test_un_seguimiento_lo_responde_el_modelo_con_el_historial(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    assert es_seguimiento("¿y para 12 años?") and es_seguimiento("pero en tapa dura")
    assert not es_seguimiento("y ahora quiero que me cuentes todo sobre las devoluciones del mes")
    modelo.respuestas = [respuesta("Para 12 años tengo estos.")]
    r = servicio_con_modelo.responder("¿y para 12 años?", "ana", "s8")
    assert r.modo == "real" and r.clasificacion is not None
    assert r.clasificacion["motivo"] == "seguimiento del mensaje anterior"


def test_el_servicio_resume_despues_del_turno(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso, registro: RegistroEventos
) -> None:
    servicio_con_modelo.resumidor = Resumidor(
        servicio_con_modelo.bd, modelo, registro, ventana=4, en_segundo_plano=False
    )
    conversar(servicio_con_modelo.bd, "s9", 5)
    modelo.respuestas = [respuesta("Hola."), respuesta("Resumen del cliente.")]
    servicio_con_modelo.responder("hola", "ana", "s9")
    resumen: dict[str, Any] | None = servicio_con_modelo.bd.resumen_de("s9")
    assert resumen is not None and resumen["texto"] == "Resumen del cliente."
