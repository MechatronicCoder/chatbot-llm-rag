"""Estado compartido de la aplicación y dependencias de FastAPI."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request, status

from app.api.limitador import LimitadorPeticiones
from app.api.sesiones import Sesiones
from app.catalogo.carga import Tienda
from app.core.config import Settings
from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.fabrica import construir_servicio

VENTANA_INTENTOS_S = 15 * 60
MAX_INTENTOS_TOTALES = 100  # por ventana, sumando todas las IP


class EstadoApp:
    """Lo que vive mientras el proceso vive: configuración, base, registro y servicio.

    El servicio de chat se construye una sola vez y con candado: leer los
    documentos y calcular embeddings cuesta, y dos peticiones simultáneas al
    arrancar no deben construirlo dos veces.
    """

    def __init__(
        self,
        ajustes: Settings,
        registro: RegistroEventos,
        bd: BaseDatos,
        tienda: Tienda,
        *,
        servicio: ServicioChat | None = None,
        fabrica: Callable[[], ServicioChat] | None = None,
    ) -> None:
        self.ajustes = ajustes
        self.registro = registro
        self.bd = bd
        self.tienda = tienda
        self.arranque = time.time()
        self.limitador = LimitadorPeticiones(ajustes.limite_chat_por_minuto)
        # Acceso de la demo: sesiones y dos topes de intentos de inicio de sesión,
        # uno por IP y otro total (contra bots que reparten intentos entre muchas IP).
        self.sesiones = Sesiones(ajustes.acceso_horas_sesion * 3600)
        self.intentos_por_ip = LimitadorPeticiones(
            ajustes.acceso_intentos_por_ip, VENTANA_INTENTOS_S
        )
        self.intentos_totales = LimitadorPeticiones(MAX_INTENTOS_TOTALES, VENTANA_INTENTOS_S)
        self._servicio = servicio
        self._fabrica = fabrica or (lambda: construir_servicio(ajustes, registro, bd))
        self._candado = threading.Lock()

    @property
    def servicio_construido(self) -> ServicioChat | None:
        return self._servicio

    @property
    def listo(self) -> bool:
        servicio = self._servicio
        return (
            servicio is not None and servicio.buscador.indexado and servicio.recomendador.indexado
        )

    def obtener_servicio(self) -> ServicioChat:
        with self._candado:
            if self._servicio is None:
                self._servicio = self._fabrica()
            return self._servicio

    def preparar(self) -> None:
        """Construye el servicio e indexa documentos y catálogo (en segundo plano)."""
        try:
            servicio = self.obtener_servicio()
            self.registro.info("corpus.indexando", chunks=len(servicio.buscador.chunks))
            total = servicio.buscador.indexar()
            info = servicio.buscador.info()
            self.registro.info(
                "corpus.listo", chunks=total, motor=info["motor"], aviso=info["aviso"]
            )
            servicio.recomendador.indexar()
            catalogo = servicio.recomendador.info()
            self.registro.info(
                "catalogo.indexado",
                libros=catalogo["libros"],
                motor=catalogo["motor"],
                aviso=catalogo["aviso"],
            )
        except Exception as exc:
            self.registro.error("corpus.error", motivo=f"{type(exc).__name__}: {exc}")

    def cerrar(self) -> None:
        servicio = self._servicio
        if servicio is None:
            return
        for cliente in (servicio.llm, servicio.buscador.embedder):
            cerrar = getattr(cliente, "cerrar", None)
            if callable(cerrar):
                cerrar()


def obtener_estado(request: Request) -> EstadoApp:
    estado: EstadoApp = request.app.state.estado
    return estado


def obtener_servicio(estado: EstadoApp = Depends(obtener_estado)) -> ServicioChat:  # noqa: B008
    return estado.obtener_servicio()


def exigir_admin(
    estado: EstadoApp = Depends(obtener_estado),  # noqa: B008
    x_admin_token: str | None = Header(default=None),
) -> None:
    """Protege logs, métricas y reportes cuando `ADMIN_TOKEN` está definido."""
    esperado = estado.ajustes.admin_token.get_secret_value()
    if not esperado:
        return
    if not x_admin_token or not secrets.compare_digest(x_admin_token, esperado):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Se requiere el encabezado X-Admin-Token válido.",
        )


def ip_cliente(request: Request, proxies_confiables: int) -> str:
    """IP del cliente para el límite de peticiones.

    Detrás de N proxies confiables, la IP real es la N-ésima de X-Forwarded-For
    contando desde la derecha: las entradas de la izquierda las escribe el
    cliente y se pueden falsificar para esquivar el límite.
    """
    if proxies_confiables > 0:
        cadena = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",")]
        cadena = [p for p in cadena if p]
        if len(cadena) >= proxies_confiables:
            return cadena[-proxies_confiables]
    return request.client.host if request.client else "desconocido"


def limitar_chat(
    request: Request,
    estado: EstadoApp = Depends(obtener_estado),  # noqa: B008
) -> None:
    """429 con `Retry-After` si el cliente supera el límite por minuto."""
    cliente = ip_cliente(request, estado.ajustes.proxies_confiables)
    permitido, espera = estado.limitador.permitir(cliente)
    if not permitido:
        estado.registro.aviso("limite.superado", cliente=cliente, reintentar_en=espera)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Límite de mensajes por minuto alcanzado; reintentar en {espera} s.",
            headers={"Retry-After": str(espera)},
        )
