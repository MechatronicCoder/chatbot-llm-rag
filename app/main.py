"""Punto de entrada: fábrica de la aplicación FastAPI.

Arranque recomendado (también es lo que ejecuta el contenedor):

    uvicorn app.main:crear_app --factory --port 8000

La fábrica permite crear una app con otra configuración o con un servicio
simulado en las pruebas, sin variables globales ni parches.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.dependencias import EstadoApp
from app.api.rutas import admin, router
from app.core.config import RAIZ, Settings, get_settings
from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat

CARPETA_FRONTEND = RAIZ / "frontend"
RUTAS_SIN_CSP = ("/docs", "/redoc", "/openapi.json")  # Swagger carga recursos de un CDN


def crear_app(
    ajustes: Settings | None = None,
    *,
    servicio: ServicioChat | None = None,
    indexar_al_iniciar: bool = True,
) -> FastAPI:
    ajustes = ajustes or get_settings()
    registro = RegistroEventos.desde_config(ajustes)
    bd = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    bd.inicializar()
    estado = EstadoApp(ajustes, registro, bd, servicio=servicio)

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        registro.info(
            "app.inicio",
            version=__version__,
            entorno=ajustes.entorno,
            llm=ajustes.llm_configurado,
            embeddings=ajustes.embeddings_configurados,
        )
        if indexar_al_iniciar:
            # El servidor abre al instante; el corpus se indexa en segundo plano.
            threading.Thread(target=estado.preparar, daemon=True, name="indexar-corpus").start()
        yield
        estado.cerrar()

    app = FastAPI(
        title="chatbot-llm-rag",
        description="Chatbot con FAQ, herramientas, RAG con MMR y registro por turno.",
        version=__version__,
        lifespan=ciclo_de_vida,
    )
    app.state.estado = estado

    @app.middleware("http")
    async def cabeceras_de_seguridad(
        request: Request, siguiente: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        respuesta = await siguiente(request)
        respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
        respuesta.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        respuesta.headers.setdefault("X-Frame-Options", "DENY")
        if not request.url.path.startswith(RUTAS_SIN_CSP):
            respuesta.headers.setdefault("Content-Security-Policy", ajustes.content_security_policy)
        return respuesta

    app.include_router(router)
    app.include_router(admin)
    if CARPETA_FRONTEND.is_dir():
        # Se monta al final: las rutas /api tienen prioridad sobre los archivos.
        app.mount("/", StaticFiles(directory=CARPETA_FRONTEND, html=True), name="frontend")
    return app
