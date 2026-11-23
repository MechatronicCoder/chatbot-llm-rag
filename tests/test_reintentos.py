"""Reintentos ante 429, 5xx y cortes de red, con espera creciente y Retry-After."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.llm.reintentos import (
    FalloPasajero,
    PoliticaReintentos,
    es_reintentable,
    post_con_reintentos,
    segundos_retry_after,
)

AHORA = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


def servidor(*salidas: httpx.Response | type[httpx.HTTPError]):  # type: ignore[no-untyped-def]
    """Responde en orden; una clase de excepción se lanza como error de red."""
    pendientes = list(salidas)
    pedidos: list[httpx.Request] = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        pedidos.append(peticion)
        salida = pendientes.pop(0)
        if isinstance(salida, httpx.Response):
            return salida
        raise salida("falla simulada", request=peticion)  # type: ignore[call-arg]

    return manejador, pedidos


def enviar(manejador, politica: PoliticaReintentos | None = None):  # type: ignore[no-untyped-def]
    esperas: list[float] = []
    avisos: list[str] = []
    respuesta = post_con_reintentos(
        httpx.Client(transport=httpx.MockTransport(manejador)),
        "https://api.test/v1/chat/completions",
        cuerpo={"x": 1},
        cabeceras={"Authorization": "Bearer k"},
        politica=politica or PoliticaReintentos(),
        esperar=esperas.append,
        azar=lambda: 0.5,
        al_reintentar=lambda motivo, _: avisos.append(motivo),
    )
    return respuesta, esperas, avisos


def test_solo_429_y_5xx_son_reintentables() -> None:
    assert es_reintentable(429) and es_reintentable(500) and es_reintentable(503)
    assert not any(es_reintentable(c) for c in (200, 400, 401, 403, 404))


def test_la_espera_crece_con_azar_y_no_pasa_del_tope() -> None:
    politica = PoliticaReintentos(base_s=1.0, espera_max_s=6.0)
    assert politica.espera(0, 0.0) == 0.5 and politica.espera(0, 1.0) == 1.0
    assert politica.espera(1, 0.5) == 1.5
    assert politica.espera(10, 1.0) == 6.0


def test_retry_after_en_segundos_o_como_fecha() -> None:
    def con(valor: str) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": valor} if valor else {})

    assert segundos_retry_after(con("3")) == 3.0
    assert segundos_retry_after(con("-2")) == 0.0
    assert segundos_retry_after(con("")) is None
    assert segundos_retry_after(con("pronto")) is None
    assert segundos_retry_after(con("Mon, 23 Nov 2026 12:00:04 GMT"), AHORA) == 4.0
    assert segundos_retry_after(con("Mon, 23 Nov 2026 12:00:02 -0000"), AHORA) == 2.0
    assert segundos_retry_after(con("Mon, 23 Nov 2026 11:00:00 GMT"), AHORA) == 0.0
    assert segundos_retry_after(con("Mon, 23 Nov 2026 12:00:04 GMT")) is not None


def test_un_429_y_un_503_se_reintentan_hasta_el_200() -> None:
    manejador, pedidos = servidor(
        httpx.Response(429, json={"error": {"message": "límite"}}),
        httpx.Response(503, text="caído"),
        httpx.Response(200, json={"ok": True}),
    )
    respuesta, esperas, avisos = enviar(manejador)
    assert respuesta.status_code == 200 and len(pedidos) == 3
    assert esperas == [0.75, 1.5]  # base·2^n, mitad fija y mitad al azar (0.5)
    assert avisos == ["HTTP 429 límite", "HTTP 503 caído"]


def test_se_respeta_retry_after_si_no_pasa_del_tope() -> None:
    manejador, _ = servidor(
        httpx.Response(429, headers={"Retry-After": "2"}), httpx.Response(200, json={})
    )
    _, esperas, _ = enviar(manejador)
    assert esperas == [2.0]


def test_si_retry_after_pide_demasiado_se_desiste_sin_esperar() -> None:
    manejador, pedidos = servidor(httpx.Response(429, headers={"Retry-After": "60"}))
    with pytest.raises(FalloPasajero, match="pide esperar 60 s"):
        enviar(manejador)
    assert len(pedidos) == 1


def test_un_error_definitivo_vuelve_enseguida() -> None:
    manejador, pedidos = servidor(httpx.Response(401, json={"error": "clave inválida"}))
    respuesta, esperas, _ = enviar(manejador)
    assert respuesta.status_code == 401 and len(pedidos) == 1 and not esperas


def test_al_agotar_los_intentos_lanza_el_ultimo_motivo() -> None:
    manejador, pedidos = servidor(*(httpx.Response(500, text="error") for _ in range(3)))
    with pytest.raises(FalloPasajero, match="HTTP 500 error"):
        enviar(manejador)
    assert len(pedidos) == 3


def test_un_corte_de_red_se_reintenta() -> None:
    manejador, pedidos = servidor(httpx.ConnectError, httpx.Response(200, json={}))
    respuesta, _, avisos = enviar(manejador)
    assert respuesta.status_code == 200 and len(pedidos) == 2
    assert avisos == ["sin respuesta (ConnectError)"]


def test_el_timeout_se_reintenta_solo_si_la_politica_lo_permite() -> None:
    manejador, pedidos = servidor(httpx.ReadTimeout, httpx.Response(200, json={}))
    assert enviar(manejador)[0].status_code == 200 and len(pedidos) == 2

    manejador, pedidos = servidor(httpx.ReadTimeout)
    with pytest.raises(FalloPasajero, match="ReadTimeout"):
        enviar(manejador, PoliticaReintentos(reintentar_timeouts=False))
    assert len(pedidos) == 1


def test_sin_reintentos_ni_aviso() -> None:
    manejador, pedidos = servidor(httpx.Response(503))
    with pytest.raises(FalloPasajero):
        post_con_reintentos(
            httpx.Client(transport=httpx.MockTransport(manejador)),
            "https://api.test/v1/embeddings",
            cuerpo={},
            cabeceras={},
            politica=PoliticaReintentos(reintentos=0),
        )
    assert len(pedidos) == 1
