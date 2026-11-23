"""Recomendador híbrido de libros para el flujo de venta.

Puntaje de cada candidato:

    puntaje = peso_similitud · similitud_normalizada + peso_valoracion · (valoración_bayesiana / 5)

- Similitud por contenido: lo que pide el cliente frente a título, autor,
  género, temas, protagonista, tono y sinopsis de cada libro. Con embeddings por
  API entiende "aprender a sumar" aunque el libro diga "matemáticas"; sin ellos
  usa TF-IDF.
- Valoración bayesiana: una media ponderada por la cantidad de votos, para que
  un 5.0 con un solo voto no gane a un 4.6 con 300 (la misma idea que usan los
  rankings de películas).
- Solo se recomiendan libros activos, con stock y dentro de la edad y la
  categoría pedidas. El índice de textos se arma una vez; precio y stock se leen
  de la base en cada recomendación.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.catalogo.modelo import texto_para_busqueda
from app.core.config import Settings
from app.core.texto import normalizar
from app.llm.embeddings import Embedder
from app.persistencia.catalogo import en_rango_de_edad
from app.servicios.rag import BuscadorDocumentos, Chunk, ParametrosRAG


@dataclass(frozen=True)
class ParametrosRecomendacion:
    candidatos: int = 20
    umbral_denso: float = 0.25
    umbral_tfidf: float = 0.03
    peso_similitud: float = 0.75
    peso_valoracion: float = 0.25
    votos_minimos: int = 20

    @classmethod
    def desde_config(cls, ajustes: Settings) -> ParametrosRecomendacion:
        return cls(
            candidatos=ajustes.reco_candidatos,
            umbral_denso=ajustes.reco_umbral,
            umbral_tfidf=ajustes.reco_umbral_tfidf,
            peso_similitud=ajustes.reco_peso_similitud,
            peso_valoracion=ajustes.reco_peso_valoracion,
            votos_minimos=ajustes.reco_votos_minimos,
        )


def valoracion_bayesiana(
    valoracion: float | None, votos: int, media: float, votos_minimos: int
) -> float:
    """(v / (v + m)) · R + (m / (v + m)) · C; sin valoración se usa la media C."""
    if valoracion is None or votos <= 0:
        return media
    return (votos / (votos + votos_minimos)) * valoracion + (
        votos_minimos / (votos + votos_minimos)
    ) * media


def rango_de_edad(libro: Mapping[str, Any]) -> str | None:
    minimo, maximo = libro.get("edad_min"), libro.get("edad_max")
    if minimo is None and maximo is None:
        return None
    if maximo is None:
        return f"desde {minimo} años"
    return f"{minimo or 0}–{maximo} años"


def resumen_libro(libro: Mapping[str, Any], largo_sinopsis: int = 180) -> dict[str, Any]:
    """Lo que necesitan el modelo y el frontend para presentar un libro."""
    sinopsis = libro.get("sinopsis") or ""
    if len(sinopsis) > largo_sinopsis:
        sinopsis = sinopsis[: largo_sinopsis - 1].rstrip() + "…"
    return {
        "isbn": libro["isbn"],
        "titulo": libro["titulo"],
        "autor": libro["autor"],
        "categoria": libro["categoria"],
        "genero": libro.get("genero") or None,
        "edad": rango_de_edad(libro),
        "formato": libro.get("formato") or None,
        "precio": libro["precio"],
        "stock": libro["stock"],
        "valoracion": libro.get("valoracion"),
        "num_valoraciones": libro.get("num_valoraciones", 0),
        "sinopsis": sinopsis,
        "portada": libro.get("portada"),
    }


class Recomendador:
    """Índice de los textos del catálogo y ordenamiento híbrido de candidatos."""

    def __init__(
        self,
        libros: list[dict[str, Any]],
        parametros: ParametrosRecomendacion | None = None,
        *,
        embedder: Embedder | None = None,
        cache: Path | None = None,
    ) -> None:
        self.parametros = p = parametros or ParametrosRecomendacion()
        chunks = [Chunk(lb["isbn"], "catalogo", 0, 0, texto_para_busqueda(lb)) for lb in libros]
        rag = ParametrosRAG(
            umbral_denso=p.umbral_denso,
            umbral_tfidf=p.umbral_tfidf,
            k_inicial=p.candidatos,
            k_final=p.candidatos,
            mmr_lambda=1.0,  # aquí manda la relevancia; la variedad la da el puntaje
        )
        self.indice = BuscadorDocumentos(chunks, rag, embedder=embedder, cache=cache)
        valoradas = [lb["valoracion"] for lb in libros if lb.get("valoracion") is not None]
        self.media = sum(valoradas) / len(valoradas) if valoradas else 0.0

    @property
    def indexado(self) -> bool:
        return self.indice.indexado

    def indexar(self) -> int:
        return self.indice.indexar()

    def info(self) -> dict[str, Any]:
        info = self.indice.info()
        return {
            "libros": info["chunks"],
            "motor": info["motor"],
            "indexado": info["indexado"],
            "aviso": info["aviso"],
        }

    def recomendar(
        self,
        necesidad: str,
        libros_vivos: Mapping[str, Mapping[str, Any]],
        *,
        edad: int | None = None,
        categoria: str | None = None,
        excluir: Iterable[str] = (),
        cantidad: int = 3,
    ) -> list[dict[str, Any]]:
        """Los `cantidad` mejores libros con stock para `necesidad`, de mayor a menor puntaje."""
        p = self.parametros
        hallazgos = self.indice.buscar(necesidad, k=p.candidatos, inicial=p.candidatos)
        excluidos = set(excluir)
        categoria_buscada = normalizar(categoria) if categoria else None
        mejor_similitud = max((h["score"] for h in hallazgos), default=0.0) or 1.0

        resultados = []
        for hallazgo in hallazgos:
            libro = libros_vivos.get(hallazgo["id"])
            if libro is None or libro["stock"] <= 0 or libro["isbn"] in excluidos:
                continue
            if categoria_buscada and libro["categoria"] != categoria_buscada:
                continue
            if edad is not None and not en_rango_de_edad(dict(libro), edad):
                continue
            bayes = valoracion_bayesiana(
                libro.get("valoracion"),
                libro.get("num_valoraciones", 0),
                self.media,
                p.votos_minimos,
            )
            puntaje = (
                p.peso_similitud * hallazgo["score"] / mejor_similitud
                + p.peso_valoracion * bayes / 5
            )
            resultados.append(
                {
                    **resumen_libro(libro),
                    "similitud": hallazgo["score"],
                    "puntaje": round(puntaje, 4),
                    "motor": hallazgo["motor"],
                }
            )
        resultados.sort(key=lambda r: r["puntaje"], reverse=True)
        return resultados[: max(1, min(int(cantidad), 5))]
