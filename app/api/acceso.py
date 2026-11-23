"""Acceso de la demo: un usuario y una clave, con la sesión en una cookie HttpOnly.

Protege el chat y el resto de la API para que nadie sin la clave gaste el crédito
de los modelos. Medidas contra bots y adivinación de la clave:

- la clave solo existe como hash PBKDF2 (``ACCESO_CLAVE_HASH``), fuera del repositorio;
- intentos limitados por IP y en total cada 15 minutos, antes de calcular el hash;
- usuario y clave se verifican siempre los dos, en tiempo constante, y el error no
  dice cuál de los dos falló;
- la sesión es un token aleatorio en una cookie HttpOnly y SameSite=Strict (Secure en
  producción) que el JavaScript de la página no puede leer;
- las rutas de salud quedan abiertas para las sondas de Azure.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencias import EstadoApp, ip_cliente, obtener_estado
from app.api.esquemas import Credenciales, EstadoAcceso
from app.core.claves import verificar_clave

COOKIE = "acceso_sesion"
RUTAS_ABIERTAS = ("/api/health", "/api/acceso")

Estado = Annotated[EstadoApp, Depends(obtener_estado)]
router = APIRouter(prefix="/api/acceso", tags=["acceso"])


def _usuario_actual(request: Request, estado: EstadoApp) -> str | None:
    return estado.sesiones.usuario_de(request.cookies.get(COOKIE))


def _estado(requerido: bool, usuario: str | None) -> dict[str, Any]:
    return {
        "requerido": requerido,
        "autenticado": not requerido or usuario is not None,
        "usuario": usuario,
    }


@router.get("/estado", response_model=EstadoAcceso)
def estado_acceso(request: Request, estado: Estado) -> dict[str, Any]:
    requerido = estado.ajustes.acceso_configurado
    return _estado(requerido, _usuario_actual(request, estado) if requerido else None)


@router.post(
    "/login",
    response_model=EstadoAcceso,
    responses={
        401: {"description": "Usuario o clave incorrectos"},
        429: {"description": "Demasiados intentos"},
    },
)
def iniciar_sesion(
    datos: Credenciales, request: Request, response: Response, estado: Estado
) -> dict[str, Any]:
    ajustes = estado.ajustes
    if not ajustes.acceso_configurado:
        return _estado(False, None)

    cliente = ip_cliente(request, ajustes.proxies_confiables)
    permitido, espera = estado.intentos_por_ip.permitir(cliente)
    if permitido:
        permitido, espera = estado.intentos_totales.permitir("todos")
    if not permitido:
        estado.registro.aviso("acceso.bloqueado", cliente=cliente, reintentar_en=espera)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiados intentos de inicio de sesión; reintentar en {espera} s.",
            headers={"Retry-After": str(espera)},
        )

    usuario_ok = secrets.compare_digest(
        datos.usuario.encode("utf-8"), ajustes.acceso_usuario.encode("utf-8")
    )
    clave_ok = verificar_clave(datos.clave, ajustes.acceso_clave_hash.get_secret_value())
    if not (usuario_ok and clave_ok):
        estado.registro.aviso("acceso.fallido", cliente=cliente)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario o clave incorrectos."
        )

    token = estado.sesiones.crear(ajustes.acceso_usuario)
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(estado.sesiones.duracion_s),
        httponly=True,
        secure=ajustes.entorno == "produccion",
        samesite="strict",
        path="/",
    )
    estado.registro.info("acceso.correcto", cliente=cliente)
    return _estado(True, ajustes.acceso_usuario)


@router.post("/logout", response_model=EstadoAcceso)
def cerrar_sesion(request: Request, response: Response, estado: Estado) -> dict[str, Any]:
    estado.sesiones.cerrar(request.cookies.get(COOKIE))
    response.delete_cookie(
        COOKIE,
        path="/",
        httponly=True,
        secure=estado.ajustes.entorno == "produccion",
        samesite="strict",
    )
    estado.registro.info("acceso.cerrado")
    return _estado(estado.ajustes.acceso_configurado, None)


def exigir_sesion(request: Request, estado: Estado) -> None:
    """401 con `X-Acceso: requerido` si el acceso está configurado y no hay sesión."""
    if not estado.ajustes.acceso_configurado or request.url.path.startswith(RUTAS_ABIERTAS):
        return
    if _usuario_actual(request, estado) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inicia sesión para usar el asistente.",
            headers={"X-Acceso": "requerido"},
        )
