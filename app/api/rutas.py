"""Rutas HTTP de la API.

Públicas: salud, libros, categorías, pedidos del cliente, documentos, chat y
borrado de la conversación. De administración (requieren `X-Admin-Token` si está
configurado): historial guardado, logs, métricas y reporte de ventas.
"""

from __future__ import annotations

import csv
import io
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from fastapi.responses import JSONResponse

from app import __version__
from app.api.dependencias import (
    EstadoApp,
    exigir_admin,
    limitar_chat,
    obtener_estado,
    obtener_servicio,
)
from app.api.esquemas import (
    ID_PATRON,
    Categoria,
    LibroDetalle,
    ListaLibros,
    ListaPedidos,
    PeticionChat,
    RespuestaChatAPI,
    SesionBorrada,
)
from app.servicios.chat import ServicioChat
from app.servicios.recomendador import resumen_libro

router = APIRouter(prefix="/api")

Estado = Annotated[EstadoApp, Depends(obtener_estado)]
Servicio = Annotated[ServicioChat, Depends(obtener_servicio)]
IdRuta = Annotated[str, Path(pattern=ID_PATRON)]


# ---------------------------------------------------------------------------
# Salud
# ---------------------------------------------------------------------------
@router.get("/health", tags=["salud"])
def salud(estado: Estado) -> dict[str, Any]:
    """Estado general: modelo, corpus, contadores y parámetros (sin claves)."""
    servicio = estado.servicio_construido
    corpus: dict[str, Any] = {
        "documentos": 0,
        "chunks": 0,
        "motor": "sin indexar",
        "indexado": False,
        "aviso": None,
    }
    faqs: dict[str, Any] = {}
    if servicio is not None:
        info = servicio.buscador.info()
        corpus = {
            "documentos": len(info["documentos"]),
            "chunks": info["chunks"],
            "motor": info["motor"],
            "indexado": info["indexado"],
            "aviso": info["aviso"],
            "palabras_por_chunk": info["palabras_por_chunk"],
        }
        faqs = servicio.faq.info()
    catalogo = (
        servicio.recomendador.info()
        if servicio is not None
        else {"libros": 0, "motor": "sin indexar", "indexado": False, "aviso": None}
    )
    configurado = servicio.llm.configurado if servicio else estado.ajustes.llm_configurado
    return {
        "estado": "ok" if estado.listo else "iniciando",
        "version": __version__,
        "uptime_s": int(time.time() - estado.arranque),
        "modelo": servicio.llm.modelo_principal if servicio else estado.ajustes.llm_model or "—",
        "modo": "real" if configurado else "demo",
        "tienda": {
            "nombre": estado.tienda.nombre,
            "moneda": estado.tienda.moneda.simbolo,
            "codigo_moneda": estado.tienda.moneda.codigo,
        },
        "corpus": corpus,
        "catalogo": catalogo,
        "faqs": faqs,
        "config": estado.ajustes.resumen(),
        **estado.bd.estadisticas(),
    }


@router.get("/health/live", tags=["salud"])
def vivo() -> dict[str, str]:
    """Liveness: el proceso responde."""
    return {"estado": "vivo"}


@router.get("/health/ready", tags=["salud"])
def listo(estado: Estado) -> JSONResponse:
    """Readiness: documentos y catálogo indexados; se pueden atender conversaciones."""
    if estado.listo:
        return JSONResponse({"estado": "listo"})
    return JSONResponse({"estado": "iniciando"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Libros, pedidos y documentos
# ---------------------------------------------------------------------------
@router.get("/libros", response_model=ListaLibros, tags=["tienda"])
def libros(
    estado: Estado,
    consulta: Annotated[str, Query(max_length=100)] = "",
    categoria: Annotated[str | None, Query(max_length=40)] = None,
    autor: Annotated[str | None, Query(max_length=100)] = None,
    edad: Annotated[int | None, Query(ge=0, le=99)] = None,
    limite: Annotated[int, Query(ge=1, le=20)] = 20,
) -> dict[str, Any]:
    """Búsqueda por palabras en título, autor, editorial, género, temas e ISBN."""
    items = estado.bd.buscar_libros(consulta, categoria, autor, edad, limite)
    return {"total": len(items), "libros": [resumen_libro(lb) for lb in items]}


@router.get(
    "/libros/{isbn}",
    response_model=LibroDetalle,
    tags=["tienda"],
    responses={404: {"description": "No hay un libro activo con ese ISBN"}},
)
def libro(estado: Estado, isbn: Annotated[str, Path(pattern=r"^[0-9-]{10,17}$")]) -> Any:
    encontrado = estado.bd.obtener_libro(isbn)
    if encontrado is None:
        raise HTTPException(status_code=404, detail=f"No hay un libro con ISBN {isbn}.")
    return encontrado


@router.get("/categorias", response_model=list[Categoria], tags=["tienda"])
def categorias(estado: Estado) -> list[dict[str, Any]]:
    return estado.bd.categorias()


@router.get("/pedidos/{usuario_id}", response_model=ListaPedidos, tags=["tienda"])
def pedidos(estado: Estado, usuario_id: IdRuta) -> dict[str, Any]:
    items = estado.bd.pedidos_de(usuario_id)
    return {
        "usuario_id": usuario_id,
        "total": len(items),
        "pendiente": round(sum(p["total"] for p in items if p["estado"] == "reservado"), 2),
        "pagado": round(sum(p["total"] for p in items if p["estado"] == "pagado"), 2),
        "pedidos": items,
    }


@router.get("/politicas", tags=["tienda"])
def politicas(servicio: Servicio) -> dict[str, Any]:
    info = servicio.buscador.info()
    return {
        "total": len(info["documentos"]),
        "documentos": info["documentos"],
        "chunks": info["chunks"],
        "motor": info["motor"],
    }


# ---------------------------------------------------------------------------
# Conversación
# ---------------------------------------------------------------------------
@router.post(
    "/chat",
    response_model=RespuestaChatAPI,
    tags=["chat"],
    dependencies=[Depends(limitar_chat)],
    responses={429: {"description": "Límite de mensajes por minuto alcanzado"}},
)
def chat(peticion: PeticionChat, servicio: Servicio) -> dict[str, Any]:
    try:
        resultado = servicio.responder(peticion.mensaje, peticion.usuario_id, peticion.session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return resultado.to_dict()


@router.delete("/sesiones/{session_id}", response_model=SesionBorrada, tags=["chat"])
def borrar_sesion(estado: Estado, session_id: IdRuta) -> dict[str, Any]:
    borrados = estado.bd.borrar_sesion(session_id)
    estado.registro.info("sesion.borrada", sesion=session_id, mensajes=borrados)
    return {"session_id": session_id, "borrados": borrados}


# ---------------------------------------------------------------------------
# Administración
# ---------------------------------------------------------------------------
admin = APIRouter(prefix="/api", dependencies=[Depends(exigir_admin)], tags=["administración"])


@admin.get("/sesion/{session_id}")
def sesion(
    estado: Estado, session_id: IdRuta, limite: Annotated[int, Query(ge=1, le=500)] = 50
) -> dict[str, Any]:
    items = estado.bd.mensajes_de(session_id, limite)
    return {"session_id": session_id, "total": len(items), "mensajes": items}


@admin.get("/logs")
def ver_logs(
    estado: Estado,
    n: Annotated[int, Query(ge=1, le=500)] = 80,
    nivel: Annotated[str | None, Query(pattern="^(DEBUG|INFO|WARN|ERROR)$")] = None,
    turno: Annotated[str | None, Query(max_length=40)] = None,
) -> dict[str, Any]:
    eventos = estado.registro.leer(limite=n, nivel=nivel, turno=turno)
    return {"nivel_minimo": estado.registro.nivel, "total": len(eventos), "eventos": eventos}


@admin.get("/metricas")
def metricas(estado: Estado) -> dict[str, Any]:
    return estado.bd.metricas()


@admin.get("/ventas.csv", response_class=Response)
def ventas_csv(estado: Estado, usuario_id: Annotated[str | None, Query()] = None) -> Response:
    buffer = io.StringIO()
    escritor = csv.writer(buffer, delimiter=";")
    columnas = {
        "pedido_id": "pedido",
        "usuario_id": "usuario",
        "isbn": "isbn",
        "titulo": "titulo",
        "autor": "autor",
        "cantidad": "cantidad",
        "precio_unitario": "precio_unitario",
        "importe": "importe",
        "envio": "envio_pedido",
        "total": "total_pedido",
        "estado": "estado",
        "creado_en": "creado_en",
    }
    escritor.writerow(columnas.values())
    for fila in estado.bd.ventas(usuario_id):
        escritor.writerow([fila[c] for c in columnas])
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=ventas.csv"},
    )
