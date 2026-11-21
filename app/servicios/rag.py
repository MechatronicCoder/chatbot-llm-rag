"""RAG: documentos PDF -> fragmentos -> vectores -> búsqueda con MMR.

- Fragmentación por oraciones con solape por palabras, para no cortar ideas a
  la mitad y conservar contexto entre fragmentos vecinos.
- Embeddings por API cuando están configurados; los vectores de los documentos
  se guardan en caché en disco y solo se recalculan si cambian los textos o el
  modelo. Siempre se construye también un índice TF-IDF: si la API de
  embeddings falla, la búsqueda sigue funcionando con menor calidad.
- MMR (Maximal Marginal Relevance): de `k_inicial` candidatos se eligen
  `k_final` que sean relevantes y distintos entre sí.
- Umbral explícito: si nada lo supera, no se devuelve contexto. Es preferible
  decir "no está en los documentos" que citar un párrafo que no responde.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.core.config import Settings
from app.llm.embeddings import Embedder, Matriz

logging.getLogger("pypdf").setLevel(logging.ERROR)

MIN_PALABRAS = 12  # un fragmento de tres palabras no responde nada

RE_FRONTERA = re.compile(r"(?<=[.!?…])\s+(?=[¿¡A-ZÁÉÍÓÚÑ0-9])")
RE_ABREVIATURA = re.compile(
    r"(?:\b(?:dr|dra|sr|sra|srta|ing|lic|prof|etc|s\.a|ltda|n|num|pág|pag|art|inc|cap|fig)\.)$",
    re.IGNORECASE,
)

_STOPWORDS_BASE = (  # noqa: SIM905 - más legible que una lista de 128 elementos
    "de la que el en y a los del se las por un para con no una su al lo como más pero sus "
    "le ya o este sí porque esta entre cuando muy sin sobre también me hasta hay donde "
    "quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos e esto "
    "mí antes algunos qué unos yo otro otras otra él tanto esa estos mucho quienes nada "
    "muchos cual poco ella estar estas algunas algo nosotros mi mis tú te ti tu tus ellas "
    "nosotras os mío mía míos mías tuyo tuya tuyos tuyas suyo suya suyos suyas nuestro "
    "nuestra nuestros nuestras esos esas estoy estás está estamos están ser soy eres "
    "somos son fue era han has hemos tiene tienen"
).split()


def _sin_acentos(palabra: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", palabra) if unicodedata.category(c) != "Mn"
    )


# El tokenizador de TF-IDF quita los acentos: la lista debe ir igual o no coincide.
STOPWORDS_ES = sorted({_sin_acentos(p) for p in _STOPWORDS_BASE})


# ---------------------------------------------------------------------------
# Parámetros
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ParametrosRAG:
    umbral_denso: float = 0.35
    umbral_tfidf: float = 0.08
    k_inicial: int = 8
    k_final: int = 3
    mmr_lambda: float = 0.5
    chunk_max_palabras: int = 150
    chunk_overlap_palabras: int = 25

    @classmethod
    def desde_config(cls, ajustes: Settings) -> ParametrosRAG:
        return cls(
            umbral_denso=ajustes.rag_umbral,
            umbral_tfidf=ajustes.rag_umbral_tfidf,
            k_inicial=ajustes.rag_k_inicial,
            k_final=ajustes.rag_k_final,
            mmr_lambda=ajustes.rag_mmr_lambda,
            chunk_max_palabras=ajustes.chunk_max_palabras,
            chunk_overlap_palabras=ajustes.chunk_overlap_palabras,
        )


# ---------------------------------------------------------------------------
# Fragmentación
# ---------------------------------------------------------------------------
def _unir_abreviaturas(oraciones: list[str]) -> list[str]:
    unidas: list[str] = []
    for oracion in oraciones:
        if unidas and RE_ABREVIATURA.search(unidas[-1]):
            unidas[-1] = f"{unidas[-1]} {oracion}"
        else:
            unidas.append(oracion)
    return unidas


def dividir_oraciones(texto: str) -> list[str]:
    limpio = " ".join(texto.split())
    return _unir_abreviaturas([o.strip() for o in RE_FRONTERA.split(limpio) if o.strip()])


def trocear(texto: str, max_palabras: int = 150, overlap_palabras: int = 25) -> list[str]:
    """Agrupa oraciones hasta `max_palabras` y repite las últimas N palabras en el siguiente."""
    chunks: list[str] = []
    actual: list[str] = []
    for oracion in dividir_oraciones(texto):
        palabras = oracion.split()
        if len(palabras) > max_palabras:  # oración gigante: se parte en bloques
            if actual:
                chunks.append(" ".join(actual))
                actual = []
            for i in range(0, len(palabras), max_palabras):
                chunks.append(" ".join(palabras[i : i + max_palabras]))
            continue
        if actual and len(actual) + len(palabras) > max_palabras:
            chunks.append(" ".join(actual))
            cola = actual[-overlap_palabras:] if overlap_palabras else []
            while cola and len(cola) + len(palabras) > max_palabras:
                cola = cola[1:]
            actual = list(cola)
        actual.extend(palabras)
    if actual:
        chunks.append(" ".join(actual))
    return chunks


@dataclass(frozen=True)
class Chunk:
    id: str
    documento: str
    pagina: int
    orden: int
    texto: str


def leer_pdf(ruta: Path) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    lector = PdfReader(str(ruta))
    return [(i + 1, pagina.extract_text() or "") for i, pagina in enumerate(lector.pages)]


def construir_chunks(carpeta: Path, parametros: ParametrosRAG) -> list[Chunk]:
    chunks: list[Chunk] = []
    for ruta in sorted(carpeta.glob("*.pdf")):
        for pagina, texto in leer_pdf(ruta):
            trozos = trocear(
                texto, parametros.chunk_max_palabras, parametros.chunk_overlap_palabras
            )
            for orden, trozo in enumerate(trozos):
                if len(trozo.split()) < MIN_PALABRAS:
                    continue
                chunks.append(
                    Chunk(
                        id=f"{ruta.stem}-p{pagina}-c{orden}",
                        documento=ruta.name,
                        pagina=pagina,
                        orden=orden,
                        texto=trozo,
                    )
                )
    return chunks


# ---------------------------------------------------------------------------
# Buscador
# ---------------------------------------------------------------------------
class BuscadorDocumentos:
    """Índice en memoria: fragmentos + matriz densa (opcional) + TF-IDF + MMR."""

    def __init__(
        self,
        chunks: list[Chunk],
        parametros: ParametrosRAG | None = None,
        *,
        embedder: Embedder | None = None,
        cache: Path | None = None,
    ) -> None:
        self.chunks = chunks
        self.parametros = parametros or ParametrosRAG()
        self.embedder = embedder
        self.cache = cache
        self.aviso = ""
        self._densa: Matriz | None = None
        self._tfidf = TfidfVectorizer(
            ngram_range=(1, 2), sublinear_tf=True, strip_accents="unicode", stop_words=STOPWORDS_ES
        )
        self._matriz_tfidf: Any = None
        self._indexado = False
        self._candado = threading.Lock()

    @classmethod
    def desde_carpeta(
        cls,
        carpeta: Path,
        parametros: ParametrosRAG,
        *,
        embedder: Embedder | None = None,
        cache: Path | None = None,
    ) -> BuscadorDocumentos:
        return cls(
            construir_chunks(carpeta, parametros), parametros, embedder=embedder, cache=cache
        )

    # -- indexación ------------------------------------------------------------
    @property
    def indexado(self) -> bool:
        return self._indexado

    @property
    def motor(self) -> str:
        if not self._indexado:
            return "sin indexar"
        return "denso" if self._densa is not None else "tfidf"

    def _huella(self) -> str:
        """Cambia si cambian los textos o el modelo: invalida la caché."""
        h = hashlib.sha256(self.embedder.identificador.encode() if self.embedder else b"")
        for chunk in self.chunks:
            h.update(chunk.texto.encode("utf-8"))
            h.update(b"\x00")
        return h.hexdigest()

    def _densa_desde_cache(self, huella: str) -> Matriz | None:
        if self.cache is None or not self.cache.exists():
            return None
        try:
            with np.load(self.cache) as datos:
                if str(datos["huella"]) == huella:
                    return np.asarray(datos["matriz"], dtype=np.float32)
        except (OSError, KeyError, ValueError):
            return None
        return None

    def _guardar_cache(self, huella: str, matriz: Matriz) -> None:
        if self.cache is None:
            return
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez(self.cache, huella=np.array(huella), matriz=matriz)
        except OSError:
            pass

    def indexar(self) -> int:
        """Construye los índices una sola vez; el candado evita indexar dos veces en paralelo."""
        with self._candado:
            if self._indexado:
                return len(self.chunks)
            textos = [c.texto for c in self.chunks]
            if textos:
                self._matriz_tfidf = self._tfidf.fit_transform(textos)
                if self.embedder is not None:
                    huella = self._huella()
                    densa = self._densa_desde_cache(huella)
                    if densa is None:
                        try:
                            densa = self.embedder.embeber(textos)
                            self._guardar_cache(huella, densa)
                        except Exception as exc:  # API caída o mal configurada
                            self.aviso = (
                                f"Embeddings no disponibles ({type(exc).__name__}); se usa TF-IDF."
                            )
                    self._densa = densa
            self._indexado = True
        return len(self.chunks)

    # -- consulta --------------------------------------------------------------
    def _similitudes(self, pregunta: str) -> tuple[np.ndarray[Any, Any], str]:
        if self._densa is not None and self.embedder is not None:
            try:
                consulta = self.embedder.embeber([pregunta])[0]
                return self._densa @ consulta, "denso"
            except Exception as exc:  # degradación por consulta, sin tumbar el turno
                self.aviso = f"Consulta sin embeddings ({type(exc).__name__}); se usó TF-IDF."
        consulta_tfidf = self._tfidf.transform([pregunta])
        return cosine_similarity(consulta_tfidf, self._matriz_tfidf)[0], "tfidf"

    def _similitud_entre(self, indices: Sequence[int], motor: str) -> np.ndarray[Any, Any]:
        if motor == "denso" and self._densa is not None:
            sub = self._densa[list(indices)]
            return sub @ sub.T
        return cosine_similarity(self._matriz_tfidf[list(indices)])

    def buscar(
        self,
        pregunta: str,
        k: int | None = None,
        *,
        inicial: int | None = None,
        lam: float | None = None,
        umbral: float | None = None,
    ) -> list[dict[str, Any]]:
        """MMR: relevancia (lam) menos redundancia (1 - lam) frente a lo ya elegido."""
        if not self.chunks:
            return []
        p = self.parametros
        k = k or p.k_final
        inicial = inicial or p.k_inicial
        lam = p.mmr_lambda if lam is None else lam

        self.indexar()
        sim_q, motor = self._similitudes(pregunta)
        if umbral is None:
            umbral = p.umbral_denso if motor == "denso" else p.umbral_tfidf
        candidatos = [
            int(i) for i in np.argsort(-sim_q)[: max(inicial, k)] if float(sim_q[i]) >= umbral
        ]
        if not candidatos:
            return []

        matriz = self._similitud_entre(candidatos, motor)
        posicion = {indice: pos for pos, indice in enumerate(candidatos)}
        elegidos: list[int] = []
        while candidatos and len(elegidos) < k:

            def puntuar(i: int) -> float:
                if not elegidos:
                    return float(sim_q[i])
                redundancia = max(float(matriz[posicion[i], posicion[j]]) for j in elegidos)
                return lam * float(sim_q[i]) - (1 - lam) * redundancia

            mejor = max(candidatos, key=puntuar)
            elegidos.append(mejor)
            candidatos.remove(mejor)

        return [
            {
                "id": self.chunks[i].id,
                "documento": self.chunks[i].documento,
                "pagina": self.chunks[i].pagina,
                "chunk": self.chunks[i].orden,
                "texto": self.chunks[i].texto,
                "score": round(float(sim_q[i]), 4),
                "motor": motor,
            }
            for i in elegidos
        ]

    def info(self) -> dict[str, Any]:
        """Estado del corpus; no indexa (un health check no debe esperar)."""
        largos = [len(c.texto.split()) for c in self.chunks]
        return {
            "documentos": sorted({c.documento for c in self.chunks}),
            "chunks": len(self.chunks),
            "motor": self.motor,
            "indexado": self._indexado,
            "aviso": self.aviso or None,
            "palabras_por_chunk": (
                [min(largos), int(np.median(largos)), max(largos)] if largos else [0, 0, 0]
            ),
        }
