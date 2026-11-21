"""Cliente de embeddings: lotes, orden, normalización y reintentos."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.llm.embeddings import ClienteEmbeddings, ErrorEmbeddings


def servidor(registro: list[dict[str, object]], fallos: list[int] | None = None):  # type: ignore[no-untyped-def]
    pendientes = list(fallos or [])

    def manejador(peticion: httpx.Request) -> httpx.Response:
        if pendientes:
            return httpx.Response(pendientes.pop(0), text="ocupado")
        cuerpo = json.loads(peticion.content)
        registro.append(cuerpo)
        datos = [
            {"index": i, "embedding": [float(len(t)), 1.0, 0.0]}
            for i, t in enumerate(cuerpo["input"])
        ]
        # el orden se invierte a propósito: el cliente debe reordenar por "index"
        return httpx.Response(200, json={"data": list(reversed(datos))})

    return manejador


def cliente(manejador, **extra: object) -> ClienteEmbeddings:  # type: ignore[no-untyped-def]
    return ClienteEmbeddings(
        "https://emb.test/v1",
        "clave",
        "text-embedding-v4",
        transporte=httpx.MockTransport(manejador),
        esperar=lambda _: None,
        **extra,  # type: ignore[arg-type]
    )


def test_lotes_orden_y_normalizacion() -> None:
    registro: list[dict[str, object]] = []
    textos = [f"texto {'x' * i}" for i in range(23)]
    matriz = cliente(servidor(registro), dimensiones=1024, lote=10).embeber(textos)

    assert [len(r["input"]) for r in registro] == [10, 10, 3]  # type: ignore[arg-type]
    assert registro[0]["dimensions"] == 1024
    assert matriz.shape == (23, 3)
    assert np.allclose(np.linalg.norm(matriz, axis=1), 1.0)
    assert matriz[0, 0] < matriz[22, 0]  # se respetó el orden por "index"


def test_reintenta_ante_429_y_5xx() -> None:
    registro: list[dict[str, object]] = []
    matriz = cliente(servidor(registro, fallos=[429, 503])).embeber(["hola"])
    assert matriz.shape == (1, 3)


def test_error_definitivo_lanza_error_embeddings() -> None:
    with pytest.raises(ErrorEmbeddings):
        cliente(servidor([], fallos=[401])).embeber(["hola"])
    with pytest.raises(ErrorEmbeddings):
        cliente(servidor([], fallos=[500, 500, 500])).embeber(["hola"])


def test_sin_dimensiones_no_se_envia_el_parametro() -> None:
    registro: list[dict[str, object]] = []
    cliente(servidor(registro)).embeber(["hola"])
    assert "dimensions" not in registro[0]


def test_identificador_cambia_con_el_modelo_o_las_dimensiones() -> None:
    a = cliente(servidor([]), dimensiones=1024).identificador
    b = cliente(servidor([]), dimensiones=512).identificador
    assert a != b


def test_error_de_red_se_reintenta() -> None:
    intentos = 0

    def manejador(peticion: httpx.Request) -> httpx.Response:
        nonlocal intentos
        intentos += 1
        if intentos == 1:
            raise httpx.ReadTimeout("lento", request=peticion)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    assert cliente(manejador).embeber(["hola"]).shape == (1, 2)
    assert intentos == 2


def test_respuesta_mal_formada_o_incompleta() -> None:
    def sin_data(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"otra": "cosa"})

    def le_falta_uno(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0]}]})

    with pytest.raises(ErrorEmbeddings, match="inválida"):
        cliente(sin_data).embeber(["hola"])
    with pytest.raises(ErrorEmbeddings, match="cantidad"):
        cliente(le_falta_uno).embeber(["uno", "dos"])


def test_lista_vacia_no_llama_a_la_api() -> None:
    assert cliente(servidor([]), dimensiones=8).embeber([]).shape == (0, 8)


def test_desde_config_y_cierre(tmp_path: Path) -> None:
    from tests.conftest import crear_ajustes

    ajustes = crear_ajustes(
        tmp_path, embeddings_base_url="https://e.test/v1", embeddings_api_key="k"
    )
    c = ClienteEmbeddings.desde_config(ajustes)
    assert c.identificador == "text-embedding-v4:1024"
    c.cerrar()
