"""Clasificador de preguntas frecuentes (FAQ) con TF-IDF.

Decide, antes de llamar al LLM, si una pregunta ya tiene respuesta verificada:

    pregunta -> vectorización -> similitud con la base de FAQs
             -> similitud alta y clara: respuesta fija (precisión, sin tokens)
             -> tema crítico sin FAQ parecida: se escala a una persona
             -> resto: el LLM responde, con las FAQs cercanas como contexto

El motor combina TF-IDF de palabras (1-2 gramos) y de caracteres (3-5): es
rápido, funciona sin red y tolera errores de tipeo y la falta de tildes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.pipeline import FeatureUnion

from app.core.seguridad import tema_critico


@dataclass(frozen=True)
class FAQ:
    id: str
    categoria: str
    critico: bool
    pregunta: str
    variaciones: tuple[str, ...]
    respuesta: str


@dataclass
class Decision:
    ruta: Literal["faq", "llm", "escalar"]
    similitud: float
    similitud_segunda: float
    faq: FAQ | None
    faq_similar: FAQ
    tema_critico: bool
    motivo: str
    candidatos: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruta": self.ruta,
            "similitud": round(self.similitud, 4),
            "similitud_segunda": round(self.similitud_segunda, 4),
            "faq_id": self.faq.id if self.faq else None,
            "faq_similar_id": self.faq_similar.id,
            "tema_critico": self.tema_critico,
            "motivo": self.motivo,
            "candidatos": self.candidatos,
        }


def cargar_banco_faq(ruta: Path) -> dict[str, Any]:
    datos: dict[str, Any] = json.loads(ruta.read_text(encoding="utf-8"))
    return datos


def construir_faqs(banco: dict[str, Any]) -> list[FAQ]:
    return [
        FAQ(
            id=f["id"],
            categoria=f["categoria"],
            critico=bool(f["critico"]),
            pregunta=f["pregunta"],
            variaciones=tuple(f.get("variaciones", [])),
            respuesta=f["respuesta"],
        )
        for f in banco["faqs"]
    ]


class IndiceFAQ:
    """Vectoriza las FAQs (pregunta y variaciones) y clasifica cada mensaje."""

    def __init__(
        self,
        faqs: list[FAQ],
        temas_criticos: list[str],
        *,
        umbral: float = 0.55,
        umbral_contexto: float = 0.25,
        margen: float = 0.03,
        top_k: int = 3,
    ) -> None:
        if not faqs:
            raise ValueError("La base de FAQs está vacía.")
        self.faqs = faqs
        self.temas_criticos = temas_criticos
        self.umbral = umbral
        self.umbral_contexto = umbral_contexto
        self.margen = margen
        self.top_k = top_k

        self.textos: list[str] = []
        self.propietarios: list[FAQ] = []
        for faq in faqs:
            for texto in (faq.pregunta, *faq.variaciones):
                self.textos.append(texto)
                self.propietarios.append(faq)

        self._union = FeatureUnion(
            [
                (
                    "palabras",
                    TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, strip_accents="unicode"),
                ),
                (
                    "caracteres",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        ngram_range=(3, 5),
                        sublinear_tf=True,
                        strip_accents="unicode",
                    ),
                ),
            ]
        )
        self._matriz = self._union.fit_transform(self.textos)

    @classmethod
    def desde_archivo(
        cls, ruta: Path, *, umbral: float, margen: float, umbral_contexto: float
    ) -> IndiceFAQ:
        banco = cargar_banco_faq(ruta)
        return cls(
            construir_faqs(banco),
            banco.get("temas_criticos", []),
            umbral=umbral,
            margen=margen,
            umbral_contexto=umbral_contexto,
        )

    def _puntajes(self, texto: str) -> list[tuple[FAQ, float]]:
        """Mejor similitud por FAQ, de mayor a menor."""
        similitudes: np.ndarray[Any, Any] = cosine_similarity(
            self._union.transform([texto]), self._matriz
        )[0]
        mejores: dict[str, float] = {}
        for indice, similitud in enumerate(similitudes):
            faq = self.propietarios[indice]
            mejores[faq.id] = max(mejores.get(faq.id, -1.0), float(similitud))
        return sorted(((f, mejores[f.id]) for f in self.faqs), key=lambda p: p[1], reverse=True)

    def clasificar(
        self, texto: str, forzar_llm: bool = False, motivo_forzado: str | None = None
    ) -> Decision:
        """Reglas, en orden: acción forzada -> LLM; similitud alta -> FAQ;
        tema crítico sin contexto -> escalar; resto -> LLM."""
        puntajes = self._puntajes(texto)
        mejor_faq, mejor = puntajes[0]
        segunda = puntajes[1][1] if len(puntajes) > 1 else 0.0
        critico = tema_critico(texto, self.temas_criticos)
        candidatos = [{"faq_id": f.id, "similitud": round(s, 4)} for f, s in puntajes[: self.top_k]]
        comun: dict[str, Any] = {
            "similitud": mejor,
            "similitud_segunda": segunda,
            "faq_similar": mejor_faq,
            "candidatos": candidatos,
        }

        if forzar_llm:
            return Decision(
                ruta="llm",
                faq=None,
                tema_critico=critico,
                motivo=motivo_forzado or "se fuerza el modelo",
                **comun,
            )
        if mejor >= self.umbral and (mejor - segunda) >= self.margen:
            return Decision(
                ruta="faq",
                faq=mejor_faq,
                tema_critico=critico,
                motivo=f"similitud {mejor:.2f} >= {self.umbral:.2f}",
                **comun,
            )
        if critico and mejor < self.umbral_contexto:
            return Decision(
                ruta="escalar",
                faq=None,
                tema_critico=True,
                motivo=(
                    "tema crítico sin FAQ suficientemente similar "
                    f"({mejor:.2f} < {self.umbral_contexto:.2f})"
                ),
                **comun,
            )
        return Decision(
            ruta="llm",
            faq=None,
            tema_critico=critico,
            motivo=f"similitud {mejor:.2f} < {self.umbral:.2f}: LLM con contexto verificado",
            **comun,
        )

    def contexto(self, texto: str, top_k: int | None = None, minimo: float = 0.0) -> str:
        """FAQs parecidas para el prompt: las que superan el umbral de contexto y `minimo`."""
        bloques = [
            f"[{faq.id} · {faq.categoria} · similitud {sim:.2f}]\n"
            f"P: {faq.pregunta}\nR: {faq.respuesta}"
            for faq, sim in self._puntajes(texto)[: top_k or self.top_k]
            if sim >= max(self.umbral_contexto, minimo)
        ]
        return "\n\n".join(bloques)

    def info(self) -> dict[str, Any]:
        return {
            "motor": "tfidf",
            "umbral_faq": self.umbral,
            "umbral_contexto": self.umbral_contexto,
            "margen": self.margen,
            "faqs": len(self.faqs),
            "textos_indexados": len(self.textos),
        }
