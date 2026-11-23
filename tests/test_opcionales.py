"""Pase a una persona, costo estimado y cambios del carrito afirmados sin la herramienta."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.turno import Acumulado
from app.servicios.verificacion import revisar_respuesta
from tests.conftest import DATOS_REALES
from tests.dobles import ModeloFalso, llamada, respuesta


def test_derivar_a_una_persona_da_el_contacto_y_avisa(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso, registro: RegistroEventos
) -> None:
    modelo.respuestas = [
        respuesta(tool_calls=[llamada("derivar_a_persona", motivo="reclamo por un libro roto")]),
        respuesta("Te atiende una persona del equipo al (01) 000-0000."),
    ]
    r = servicio_con_modelo.responder("quiero hablar con una persona", "ana", "s-p")
    assert r.ruta == "escalar"
    resultado = modelo.llamadas[1]["mensajes"][-1]["content"]
    assert "atencion@ejemplo.pe" in resultado and "lunes a sábado" in resultado
    avisos = [e for e in registro.leer(nivel="WARN") if e["evento"] == "derivacion.solicitada"]
    assert avisos and avisos[0]["motivo"] == "reclamo por un libro roto"


def test_sin_contacto_configurado_no_se_deriva(servicio: ServicioChat) -> None:
    servicio.bd.tienda.contacto = None
    r = servicio.herramientas.ejecutar("derivar_a_persona", {"motivo": "x"}, "a")
    assert "contacto" in r["error"]


def test_el_contacto_coincide_con_las_politicas() -> None:
    from app.catalogo.carga import cargar_tienda

    contacto = cargar_tienda(DATOS_REALES / "tienda.json").contacto
    politicas = (DATOS_REALES / "documentos" / "politicas_libreria.md").read_text(encoding="utf-8")
    assert contacto is not None
    assert contacto.telefono in politicas and contacto.correo in politicas
    assert contacto.horario in politicas


def test_afirmar_un_cambio_del_carrito_exige_la_herramienta(bd: BaseDatos) -> None:
    texto = "Listo, ya quité El Silmarillion de tu carrito."
    problema = revisar_respuesta(texto, Acumulado(), bd, "ana", "mejor quita El Silmarillion")
    assert problema is not None and "actualizar_carrito" in problema.instruccion
    hecho = Acumulado(usadas=[{"nombre": "actualizar_carrito", "ok": True}])
    assert revisar_respuesta(texto, hecho, bd, "ana", "mejor quita El Silmarillion") is None


def test_las_metricas_estiman_el_costo_si_hay_precios(tmp_path: Path) -> None:
    from tests.test_api import cliente_con

    sin_precio = cliente_con(tmp_path / "a")
    assert "costo_estimado_usd" not in sin_precio.get("/api/metricas").json()
    con_precio: TestClient = cliente_con(
        tmp_path / "b", precio_entrada_usd_millon=0.4, precio_salida_usd_millon=1.2
    )
    estado = con_precio.app.state.estado  # type: ignore[attr-defined]
    estado.bd.guardar_turno("t-1", "s-1", "a", "hola", "buenas", tokens=(1_000_000, 500_000))
    assert con_precio.get("/api/metricas").json()["costo_estimado_usd"] == 1.0


def test_pedir_una_persona_no_lo_responde_la_faq() -> None:
    from app.servicios.turno import RE_ACCION

    assert RE_ACCION.search("mi libro llegó roto y quiero hablar con una persona del equipo")
    assert RE_ACCION.search("¿puedo hablar con un asesor?")
