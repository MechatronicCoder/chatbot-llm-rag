"""Recomendador híbrido: contenido + valoración bayesiana + filtros de stock, edad y categoría."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest

from app.llm.embeddings import Matriz, normalizar_filas
from app.persistencia.db import BaseDatos
from app.servicios.recomendador import (
    ParametrosRecomendacion,
    Recomendador,
    rango_de_edad,
    resumen_libro,
    valoracion_bayesiana,
)
from tests.datos_prueba import (
    ISBN_AGOTADO,
    ISBN_DISTOPIA,
    ISBN_DRAGON,
    ISBN_GOBLIN,
    ISBN_MAGO,
    ISBN_SUMAS,
    ISBN_UN_VOTO,
)


def vivos(bd: BaseDatos) -> dict[str, dict[str, Any]]:
    return {lb["isbn"]: lb for lb in bd.libros_activos()}


@pytest.fixture()
def recomendador(bd: BaseDatos) -> Recomendador:
    return Recomendador(bd.libros_activos())


def isbns(resultados: list[dict[str, Any]]) -> list[str]:
    return [r["isbn"] for r in resultados]


def test_valoracion_bayesiana() -> None:
    assert valoracion_bayesiana(None, 0, 4.0, 20) == 4.0
    assert valoracion_bayesiana(5.0, 0, 4.0, 20) == 4.0
    assert valoracion_bayesiana(5.0, 1, 4.0, 20) == pytest.approx(4.0476, abs=1e-3)
    assert valoracion_bayesiana(4.8, 600, 4.0, 20) == pytest.approx(4.774, abs=1e-3)


def test_rango_de_edad_y_resumen() -> None:
    assert rango_de_edad({"edad_min": 4, "edad_max": 6}) == "4–6 años"
    assert rango_de_edad({"edad_min": 15, "edad_max": None}) == "desde 15 años"
    assert rango_de_edad({"edad_min": None, "edad_max": 8}) == "0–8 años"
    assert rango_de_edad({}) is None
    largo = {"isbn": "x", "titulo": "t", "autor": "a", "categoria": "c", "precio": 1, "stock": 1,
             "sinopsis": "palabra " * 60}  # fmt: skip
    assert resumen_libro(largo, largo_sinopsis=50)["sinopsis"].endswith("…")


def test_entiende_la_necesidad_y_filtra_por_edad(recomendador: Recomendador, bd: BaseDatos) -> None:
    sumar = recomendador.recomendar("aprender a sumar números", vivos(bd), edad=5)
    assert isbns(sumar)[0] == ISBN_SUMAS
    assert all(r["edad"] == "4–6 años" or r["edad"] is None for r in sumar)
    goblin = recomendador.recomendar("fantasía con un goblin de protagonista", vivos(bd))
    assert isbns(goblin)[0] == ISBN_GOBLIN
    adulto = recomendador.recomendar("distopía vigilancia", vivos(bd), edad=8)
    assert ISBN_DISTOPIA not in isbns(adulto)


def test_nunca_recomienda_sin_stock(recomendador: Recomendador, bd: BaseDatos) -> None:
    resultados = recomendador.recomendar("ladrón del tiempo magia", vivos(bd), cantidad=5)
    assert ISBN_AGOTADO not in isbns(resultados)


def test_categoria_exclusion_y_cantidad(recomendador: Recomendador, bd: BaseDatos) -> None:
    fantasia = recomendador.recomendar("fantasía magia aventura", vivos(bd), cantidad=5)
    assert len(fantasia) <= 5
    solo_juvenil = recomendador.recomendar("fantasía magia", vivos(bd), categoria="Juvenil")
    assert solo_juvenil and all(r["categoria"] == "juvenil" for r in solo_juvenil)
    sin_mago = recomendador.recomendar("magia escuela", vivos(bd), excluir=[ISBN_MAGO])
    assert ISBN_MAGO not in isbns(sin_mago)
    assert len(recomendador.recomendar("fantasía", vivos(bd), cantidad=0)) == 1


def test_la_valoracion_desempata_con_votos(recomendador: Recomendador, bd: BaseDatos) -> None:
    """Dos libros casi iguales: gana el 4.8 con 600 votos, no el 5.0 con un solo voto."""
    resultados = recomendador.recomendar("compañía viaja montaña dragón tesoro", vivos(bd))
    orden = isbns(resultados)
    assert orden.index(ISBN_DRAGON) < orden.index(ISBN_UN_VOTO)


def test_sin_coincidencias_no_inventa(recomendador: Recomendador, bd: BaseDatos) -> None:
    assert recomendador.recomendar("receta de cocina japonesa", vivos(bd)) == []
    assert Recomendador([]).recomendar("fantasía", {}) == []


def test_info_y_parametros(recomendador: Recomendador) -> None:
    assert recomendador.indexado is False
    recomendador.indexar()
    info = recomendador.info()
    assert info["indexado"] and info["motor"] == "tfidf" and info["libros"] == 10
    assert recomendador.media == pytest.approx(4.6, abs=0.1)
    assert Recomendador([]).media == 0.0


def test_parametros_desde_la_configuracion(tmp_path: Any) -> None:
    from tests.conftest import crear_ajustes

    ajustes = crear_ajustes(tmp_path, reco_peso_similitud=0.6, reco_votos_minimos=5)
    parametros = ParametrosRecomendacion.desde_config(ajustes)
    assert parametros.peso_similitud == 0.6 and parametros.votos_minimos == 5


class EmbedderTemas:
    """Embeddings falsos por temas: suficiente para probar el motor denso."""

    VOCABULARIO = ("sumar", "goblin", "magia", "distopia", "dragon", "mar")

    @property
    def identificador(self) -> str:
        return "temas:6"

    def embeber(self, textos: Sequence[str]) -> Matriz:
        filas = [[t.lower().count(p) + 0.01 for p in self.VOCABULARIO] for t in textos]
        return normalizar_filas(np.asarray(filas, dtype=np.float32))


def test_con_embeddings_usa_el_motor_denso(bd: BaseDatos) -> None:
    recomendador = Recomendador(
        bd.libros_activos(), ParametrosRecomendacion(umbral_denso=0.3), embedder=EmbedderTemas()
    )
    resultados = recomendador.recomendar("un goblin", vivos(bd))
    assert resultados[0]["isbn"] == ISBN_GOBLIN
    assert resultados[0]["motor"] == "denso"
    assert recomendador.info()["motor"] == "denso"
