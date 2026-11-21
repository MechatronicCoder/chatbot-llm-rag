"""Fábrica del servicio de chat: arma todas las piezas a partir de la configuración."""

from __future__ import annotations

from app.core.config import Settings
from app.core.logs import RegistroEventos
from app.llm.cliente import ClienteLLM
from app.llm.embeddings import ClienteEmbeddings
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.faq import IndiceFAQ
from app.servicios.rag import BuscadorDocumentos, ParametrosRAG
from app.servicios.turno import AjustesChat


def construir_servicio(ajustes: Settings, registro: RegistroEventos, bd: BaseDatos) -> ServicioChat:
    """Lee PDFs, FAQs y prompt; conecta LLM y embeddings si están configurados."""
    embedder = ClienteEmbeddings.desde_config(ajustes) if ajustes.embeddings_configurados else None
    buscador = BuscadorDocumentos.desde_carpeta(
        ajustes.carpeta_datos / "pdfs",
        ParametrosRAG.desde_config(ajustes),
        embedder=embedder,
        cache=ajustes.cache_embeddings,
    )
    faq = IndiceFAQ.desde_archivo(
        ajustes.carpeta_datos / "faq.json",
        umbral=ajustes.faq_umbral,
        margen=ajustes.faq_margen,
        umbral_contexto=ajustes.faq_contexto_min,
    )
    prompt = (ajustes.carpeta_datos / "prompt_sistema.md").read_text(encoding="utf-8")
    return ServicioChat(
        bd=bd,
        registro=registro,
        buscador=buscador,
        llm=ClienteLLM.desde_config(ajustes),
        faq=faq,
        prompt_sistema=prompt,
        ajustes=AjustesChat.desde_config(ajustes),
    )
