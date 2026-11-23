"""Fábrica del servicio de chat: arma todas las piezas a partir de la configuración."""

from __future__ import annotations

from app.catalogo.carga import Tienda, cargar_tienda
from app.core.config import Settings
from app.core.logs import RegistroEventos
from app.llm.cliente import ClienteLLM
from app.llm.embeddings import ClienteEmbeddings
from app.llm.tope import ModeloConTope
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.faq import IndiceFAQ
from app.servicios.rag import BuscadorDocumentos, ParametrosRAG
from app.servicios.recomendador import ParametrosRecomendacion, Recomendador
from app.servicios.resumen import Resumidor
from app.servicios.turno import AjustesChat


def preparar_prompt(plantilla: str, tienda: Tienda) -> str:
    """Completa `{tienda}` y `{moneda}` del prompt con los datos de `datos/tienda.json`."""
    moneda = f"{tienda.moneda.simbolo} ({tienda.moneda.codigo})"
    return plantilla.replace("{tienda}", tienda.nombre).replace("{moneda}", moneda)


def construir_servicio(ajustes: Settings, registro: RegistroEventos, bd: BaseDatos) -> ServicioChat:
    """Lee documentos, FAQs, catálogo y prompt; conecta LLM y embeddings si están configurados.

    `bd` ya debe estar inicializada: el recomendador indexa los libros activos.
    """
    datos = ajustes.carpeta_datos
    embedder = ClienteEmbeddings.desde_config(ajustes) if ajustes.embeddings_configurados else None
    buscador = BuscadorDocumentos.desde_carpeta(
        datos / "documentos",
        ParametrosRAG.desde_config(ajustes),
        embedder=embedder,
        cache=ajustes.cache_embeddings,
    )
    recomendador = Recomendador(
        bd.libros_activos(),
        ParametrosRecomendacion.desde_config(ajustes),
        embedder=embedder,
        cache=ajustes.cache_embeddings_catalogo,
    )
    faq = IndiceFAQ.desde_archivo(
        datos / "faq.json",
        umbral=ajustes.faq_umbral,
        margen=ajustes.faq_margen,
        umbral_contexto=ajustes.faq_contexto_min,
    )
    tienda = cargar_tienda(datos / "tienda.json")
    prompt = preparar_prompt((datos / "prompt_sistema.md").read_text(encoding="utf-8"), tienda)
    llm = ModeloConTope(
        ClienteLLM.desde_config(ajustes, registro),
        bd.tokens_ultimas_24h,
        ajustes.llm_tokens_por_dia,
    )
    return ServicioChat(
        bd=bd,
        registro=registro,
        buscador=buscador,
        recomendador=recomendador,
        llm=llm,
        faq=faq,
        prompt_sistema=prompt,
        tienda=tienda,
        ajustes=AjustesChat.desde_config(ajustes),
        resumidor=Resumidor(bd, llm, registro, ajustes.max_mensajes)
        if ajustes.resumen_activo
        else None,
    )
