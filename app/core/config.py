"""Configuración tipada: un solo lugar para leer el `.env` y validar sus valores.

Cada variable de entorno corresponde a un campo de `Settings` con el mismo
nombre en mayúsculas (`LLM_MODEL` -> `llm_model`). Si un valor no tiene el tipo
o el rango esperado, la aplicación falla al arrancar con un mensaje claro, en
lugar de fallar a mitad de una conversación.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.claves import hash_valido

RAIZ = Path(__file__).resolve().parents[2]

CSP_POR_DEFECTO = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class Settings(BaseSettings):
    """Variables de entorno de la aplicación (ver `.env.example`)."""

    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- aplicación -----------------------------------------------------------
    entorno: Literal["local", "produccion", "pruebas"] = "local"
    carpeta_datos: Path = RAIZ / "datos"
    db_path: Path = RAIZ / "var" / "tienda.db"
    cache_embeddings: Path = RAIZ / "var" / "embeddings.npz"
    cache_embeddings_catalogo: Path = RAIZ / "var" / "embeddings_catalogo.npz"

    # -- logs -----------------------------------------------------------------
    log_archivo: Path = RAIZ / "var" / "logs" / "eventos.jsonl"
    log_consola: bool = True
    log_formato: Literal["texto", "json"] = "texto"
    log_nivel: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"
    log_rotacion_mb: float = Field(default=2.0, gt=0)
    llm_lento_ms: int = Field(default=4000, ge=0)

    # -- LLM principal y respaldo (cualquier API compatible con OpenAI) -------
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_2_base_url: str = ""
    llm_2_api_key: SecretStr = SecretStr("")
    llm_2_model: str = ""
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_temperatura: float = Field(default=0.3, ge=0, le=2)
    llm_max_tokens: int = Field(default=700, ge=50, le=8192)
    llm_extra_body: dict[str, Any] = Field(default_factory=dict)
    # Tope de tokens del chat en las últimas 24 horas; al pasarlo se responde en
    # modo demo. 0 = sin tope.
    llm_tokens_por_dia: int = Field(default=2_000_000, ge=0)

    # -- reintentos ante 429, 5xx o cortes de red (chat y embeddings) ---------
    api_reintentos: int = Field(default=2, ge=0, le=5)
    api_espera_max_s: float = Field(default=6.0, ge=0, le=60)

    # -- embeddings por API ---------------------------------------------------
    embeddings_base_url: str = ""
    embeddings_api_key: SecretStr = SecretStr("")
    embeddings_model: str = "text-embedding-v4"
    embeddings_dimensiones: int = Field(default=1024, ge=0)
    embeddings_lote: int = Field(default=10, ge=1, le=256)
    embeddings_timeout_s: float = Field(default=30.0, gt=0)

    # -- clasificador de preguntas frecuentes ---------------------------------
    faq_umbral: float = Field(default=0.55, ge=0, le=1)
    faq_margen: float = Field(default=0.03, ge=0, le=1)
    faq_contexto_min: float = Field(default=0.25, ge=0, le=1)

    # -- recuperación de documentos (RAG) -------------------------------------
    rag_umbral: float = Field(default=0.35, ge=-1, le=1)
    rag_umbral_tfidf: float = Field(default=0.08, ge=0, le=1)
    rag_k_inicial: int = Field(default=8, ge=1, le=50)
    rag_k_final: int = Field(default=3, ge=1, le=10)
    rag_mmr_lambda: float = Field(default=0.5, ge=0, le=1)
    chunk_max_palabras: int = Field(default=150, ge=20, le=1000)
    chunk_overlap_palabras: int = Field(default=25, ge=0, le=200)

    # -- recomendación de libros (híbrido: contenido + valoración) -------------
    reco_candidatos: int = Field(default=20, ge=3, le=100)
    reco_umbral: float = Field(default=0.25, ge=-1, le=1)
    reco_umbral_tfidf: float = Field(default=0.03, ge=0, le=1)
    reco_peso_similitud: float = Field(default=0.75, ge=0, le=1)
    reco_peso_valoracion: float = Field(default=0.25, ge=0, le=1)
    reco_votos_minimos: int = Field(default=20, ge=0, le=100000)

    # -- conversación ---------------------------------------------------------
    max_mensajes: int = Field(default=8, ge=0, le=50)
    max_vueltas: int = Field(default=3, ge=1, le=10)
    limite_mensaje: int = Field(default=2000, ge=10, le=20000)

    # -- protección del servicio publicado ------------------------------------
    limite_chat_por_minuto: int = Field(default=20, ge=0)
    proxies_confiables: int = Field(default=0, ge=0, le=5)
    admin_token: SecretStr = SecretStr("")
    # Acceso de la demo: un usuario y el hash de su clave (scripts/hash_clave.py).
    # Vacíos en local = sin login; en producción son obligatorios.
    acceso_usuario: str = Field(default="", max_length=60)
    acceso_clave_hash: SecretStr = SecretStr("")
    acceso_horas_sesion: int = Field(default=8, ge=1, le=72)
    # Intentos de inicio de sesión por IP cada 15 minutos (contra adivinar la clave)
    acceso_intentos_por_ip: int = Field(default=5, ge=1, le=50)
    content_security_policy: str = CSP_POR_DEFECTO

    # -- validaciones ---------------------------------------------------------
    @field_validator(
        "carpeta_datos", "db_path", "cache_embeddings", "cache_embeddings_catalogo", "log_archivo"
    )
    @classmethod
    def _ruta_absoluta(cls, valor: Path) -> Path:
        """Las rutas relativas del `.env` se resuelven desde la raíz del repo."""
        return valor if valor.is_absolute() else RAIZ / valor

    @field_validator("llm_base_url", "llm_2_base_url", "embeddings_base_url")
    @classmethod
    def _sin_barra_final(cls, valor: str) -> str:
        return valor.strip().rstrip("/")

    @model_validator(mode="after")
    def _coherencia(self) -> Settings:
        if self.chunk_overlap_palabras >= self.chunk_max_palabras:
            raise ValueError("CHUNK_OVERLAP_PALABRAS debe ser menor que CHUNK_MAX_PALABRAS.")
        if self.rag_k_final > self.rag_k_inicial:
            raise ValueError("RAG_K_FINAL no puede ser mayor que RAG_K_INICIAL.")
        if self.entorno == "produccion" and not self.admin_token.get_secret_value():
            raise ValueError(
                "ADMIN_TOKEN es obligatorio en producción: protege logs, métricas y ventas."
            )
        clave_hash = self.acceso_clave_hash.get_secret_value()
        if bool(self.acceso_usuario) != bool(clave_hash):
            raise ValueError("ACCESO_USUARIO y ACCESO_CLAVE_HASH se definen juntos.")
        if clave_hash and not hash_valido(clave_hash):
            raise ValueError(
                "ACCESO_CLAVE_HASH no tiene el formato esperado; generarlo con "
                "python scripts/hash_clave.py."
            )
        if self.entorno == "produccion" and not self.acceso_configurado:
            raise ValueError(
                "ACCESO_USUARIO y ACCESO_CLAVE_HASH son obligatorios en producción: "
                "sin ellos cualquiera podría usar el chat y gastar el crédito de la API."
            )
        return self

    # -- vistas de solo lectura -----------------------------------------------
    @property
    def llm_configurado(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key.get_secret_value() and self.llm_model)

    @property
    def acceso_configurado(self) -> bool:
        return bool(self.acceso_usuario and self.acceso_clave_hash.get_secret_value())

    @property
    def embeddings_configurados(self) -> bool:
        return bool(
            self.embeddings_base_url
            and self.embeddings_api_key.get_secret_value()
            and self.embeddings_model
        )

    def resumen(self) -> dict[str, Any]:
        """Parámetros ajustables que `/api/health` muestra. Nunca incluye claves."""
        return {
            "entorno": self.entorno,
            "faq_umbral": self.faq_umbral,
            "faq_margen": self.faq_margen,
            "rag_umbral": self.rag_umbral,
            "rag_umbral_tfidf": self.rag_umbral_tfidf,
            "rag_k": [self.rag_k_inicial, self.rag_k_final],
            "rag_mmr_lambda": self.rag_mmr_lambda,
            "chunk": [self.chunk_max_palabras, self.chunk_overlap_palabras],
            "embeddings": self.embeddings_model if self.embeddings_configurados else None,
            "reco_pesos": [self.reco_peso_similitud, self.reco_peso_valoracion],
            "reco_votos_minimos": self.reco_votos_minimos,
            "limite_chat_por_minuto": self.limite_chat_por_minuto,
            "llm_tokens_por_dia": self.llm_tokens_por_dia,
            "api_reintentos": self.api_reintentos,
            "acceso_requerido": self.acceso_configurado,
            "log_nivel": self.log_nivel,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Instancia única, leída del entorno y del `.env` la primera vez."""
    return Settings()
