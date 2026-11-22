"""Punto de entrada: fábrica de la aplicación FastAPI.

Arranque recomendado (también es lo que ejecuta el contenedor):

    uvicorn app.main:crear_app --factory --port 8000

La fábrica permite crear una app con otra configuración o con un servicio
simulado en las pruebas, sin variables globales ni parches.
"""

from __future__ import annotations

import mimetypes
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.dependencias import EstadoApp
from app.api.rutas import admin, router
from app.catalogo.carga import cargar_tienda
from app.core.config import RAIZ, Settings, get_settings
from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat

# En Windows el tipo de .js puede venir del registro como text/plain; con nosniff,
# el navegador no ejecutaría los módulos del frontend. Se fija explícitamente.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")

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
    tienda = cargar_tienda(ajustes.carpeta_datos / "tienda.json")
    bd = BaseDatos(ajustes.db_path, ajustes.carpeta_datos / "catalogo.json")
    bd.inicializar()  # valida y sincroniza el catálogo: con datos inválidos, no arranca
    estado = EstadoApp(ajustes, registro, bd, tienda, servicio=servicio)

    @asynccontextmanager
    async def ciclo_de_vida(_: FastAPI) -> AsyncIterator[None]:
        registro.info(
            "app.inicio",
            version=__version__,
            entorno=ajustes.entorno,
            llm=ajustes.llm_configurado,
            embeddings=ajustes.embeddings_configurados,
        )
        sincronizacion = bd.ultima_sincronizacion
        registro.info(
            "catalogo.sincronizado",
            nuevos=sincronizacion.get("nuevos", 0),
            actualizados=sincronizacion.get("actualizados", 0),
            retirados=sincronizacion.get("retirados", 0),
        )
        if indexar_al_iniciar:
            # El servidor abre al instante; el corpus se indexa en segundo plano.
            threading.Thread(target=estado.preparar, daemon=True, name="indexar-corpus").start()
        yield
        estado.cerrar()

    app = FastAPI(
        title="chatbot-llm-rag",
        description="Asistente de librería: FAQ, recomendación híbrida, venta por herramientas, "
        "RAG con MMR y registro por turno.",
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
