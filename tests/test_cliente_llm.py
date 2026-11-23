"""Cliente de chat contra un servidor simulado (httpx.MockTransport)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.logs import RegistroEventos
from app.llm.cliente import ClienteLLM, Proveedor, SinProveedores

PRINCIPAL = Proveedor("principal", "https://principal.test/v1", "clave-1", "modelo-a")
RESPALDO = Proveedor("respaldo", "https://respaldo.test/v1", "clave-2", "modelo-b")


def respuesta_ok(contenido: str = "hola", **mensaje: Any) -> dict[str, Any]:
    return {
        "model": "modelo-real",
        "choices": [{"message": {"role": "assistant", "content": contenido, **mensaje}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


def cliente(
    manejador: Any, *proveedores: Proveedor, registro: RegistroEventos | None = None
) -> ClienteLLM:
    return ClienteLLM(
        proveedores or (PRINCIPAL,),
        transporte=httpx.MockTransport(manejador),
        esperar=lambda _: None,
        registro=registro,
    )


def test_respuesta_de_texto_normalizada() -> None:
    peticiones: list[httpx.Request] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        peticiones.append(peticion)
        return httpx.Response(200, json=respuesta_ok("buenas"))

    r = cliente(manejador).chat([{"role": "user", "content": "hola"}], max_tokens=50)
    assert r.texto == "buenas"
    assert r.modelo == "modelo-real"
    assert r.uso == {"prompt_tokens": 12, "completion_tokens": 3}
    cuerpo = json.loads(peticiones[0].content)
    assert peticiones[0].url == "https://principal.test/v1/chat/completions"
    assert peticiones[0].headers["Authorization"] == "Bearer clave-1"
    assert cuerpo["max_tokens"] == 50
    assert "tools" not in cuerpo


def test_herramientas_en_formato_openai_y_argumentos_como_dict() -> None:
    def manejador(peticion: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(peticion.content)
        assert cuerpo["tools"][0] == {"type": "function", "function": {"name": "f"}}
        llamada = {"id": "c1", "type": "function"}
        llamada["function"] = {"name": "f", "arguments": '{"x": 1}'}  # type: ignore[assignment]
        return httpx.Response(200, json=respuesta_ok("", tool_calls=[llamada]))

    r = cliente(manejador).chat([{"role": "user", "content": "x"}], [{"name": "f"}])
    assert r.tool_calls == [{"id": "c1", "name": "f", "arguments": {"x": 1}}]


def test_argumentos_mal_formados_no_rompen() -> None:
    def manejador(_: httpx.Request) -> httpx.Response:
        llamada = {"id": "c1", "function": {"name": "f", "arguments": "{roto"}}
        return httpx.Response(200, json=respuesta_ok("", tool_calls=[llamada]))

    r = cliente(manejador).chat([{"role": "user", "content": "x"}], [{"name": "f"}])
    assert r.tool_calls[0]["arguments"] == {}


@pytest.mark.parametrize("fallo", [401, 429, 500])
def test_si_el_principal_falla_responde_el_respaldo(fallo: int) -> None:
    def manejador(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.host == "principal.test":
            return httpx.Response(fallo, json={"error": {"message": "falló"}})
        return httpx.Response(200, json=respuesta_ok("desde respaldo"))

    r = cliente(manejador, PRINCIPAL, RESPALDO).chat([{"role": "user", "content": "x"}])
    assert r.texto == "desde respaldo"
    assert r.proveedor == "respaldo"


def test_error_de_red_pasa_al_respaldo() -> None:
    def manejador(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.host == "principal.test":
            raise httpx.ConnectTimeout("sin red", request=peticion)
        return httpx.Response(200, json=respuesta_ok("ok"))

    assert cliente(manejador, PRINCIPAL, RESPALDO).chat([]).proveedor == "respaldo"


def test_todos_fallan_lanza_sin_proveedores_con_el_detalle() -> None:
    def manejador(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="caído")

    with pytest.raises(SinProveedores) as error:
        cliente(manejador, PRINCIPAL, RESPALDO).chat([])
    assert len(error.value.intentos) == 2
    assert "clave-1" not in str(error.value)


def test_sin_configuracion_no_intenta_llamar() -> None:
    llamado = False

    def manejador(_: httpx.Request) -> httpx.Response:
        nonlocal llamado
        llamado = True
        return httpx.Response(200, json=respuesta_ok())

    incompleto = Proveedor("principal", "https://x.test", "", "modelo")
    c = cliente(manejador, incompleto)
    assert not c.configurado
    with pytest.raises(SinProveedores):
        c.chat([])
    assert not llamado


def test_respuesta_200_con_formato_inesperado_pasa_al_respaldo() -> None:
    def manejador(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.host == "principal.test":
            return httpx.Response(200, json={"sin": "choices"})
        return httpx.Response(200, json=respuesta_ok("ok"))

    r = cliente(manejador, PRINCIPAL, RESPALDO).chat([])
    assert r.proveedor == "respaldo"


def test_error_con_detalle_de_texto_plano() -> None:
    def manejador(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "modelo inexistente"})

    with pytest.raises(SinProveedores, match="modelo inexistente"):
        cliente(manejador).chat([])


def test_argumentos_que_ya_vienen_como_dict() -> None:
    from app.llm.cliente import _argumentos

    assert _argumentos({"a": 1}) == {"a": 1}
    assert _argumentos("[1, 2]") == {}


def test_desde_config_y_cierre(tmp_path: Path) -> None:
    from tests.conftest import crear_ajustes

    ajustes = crear_ajustes(
        tmp_path,
        llm_base_url="https://a.test/v1/",
        llm_api_key="k1",
        llm_model="m1",
        llm_2_base_url="https://b.test/v1",
        llm_2_api_key="k2",
        llm_2_model="m2",
    )
    c = ClienteLLM.desde_config(ajustes)
    assert [p.nombre for p in c.proveedores] == ["principal", "respaldo"]
    assert c.proveedores[0].base_url == "https://a.test/v1"
    assert c.modelo_principal == "m1"
    c.cerrar()
    assert ClienteLLM([]).modelo_principal == "sin modelo"


# -- reintentos ------------------------------------------------------------------
def eventos(registro: RegistroEventos) -> list[dict[str, Any]]:
    return registro.leer(limite=20)


def test_un_429_pasajero_se_reintenta_con_el_mismo_proveedor(registro: RegistroEventos) -> None:
    llamadas = 0

    def manejador(_: httpx.Request) -> httpx.Response:
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            return httpx.Response(429, json={"error": {"message": "límite de uso"}})
        return httpx.Response(200, json=respuesta_ok("al segundo intento"))

    r = cliente(manejador, PRINCIPAL, RESPALDO, registro=registro).chat([])
    assert r.proveedor == "principal" and llamadas == 2
    reintento = next(e for e in eventos(registro) if e["evento"] == "llm.reintento")
    assert reintento["proveedor"] == "principal" and "429" in reintento["motivo"]


def test_si_el_principal_no_se_recupera_el_respaldo_queda_registrado(
    registro: RegistroEventos,
) -> None:
    def manejador(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.host == "principal.test":
            return httpx.Response(503, text="caído")
        return httpx.Response(200, json=respuesta_ok("ok"))

    assert cliente(manejador, PRINCIPAL, RESPALDO, registro=registro).chat([]).proveedor == (
        "respaldo"
    )
    nombres = [e["evento"] for e in eventos(registro)]
    assert nombres.count("llm.reintento") == 2 and "llm.respaldo" in nombres


def test_un_timeout_del_chat_pasa_al_respaldo_sin_repetir() -> None:
    llamadas: list[str] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(peticion.url.host)
        if peticion.url.host == "principal.test":
            raise httpx.ReadTimeout("lento", request=peticion)
        return httpx.Response(200, json=respuesta_ok("ok"))

    assert cliente(manejador, PRINCIPAL, RESPALDO).chat([]).proveedor == "respaldo"
    assert llamadas == ["principal.test", "respaldo.test"]


def test_desde_config_toma_la_politica_de_reintentos(tmp_path: Path) -> None:
    from tests.conftest import crear_ajustes

    ajustes = crear_ajustes(tmp_path, api_reintentos=4, api_espera_max_s=3)
    c = ClienteLLM.desde_config(ajustes)
    assert c.politica.reintentos == 4 and c.politica.espera_max_s == 3
    assert not c.politica.reintentar_timeouts
    c.cerrar()
