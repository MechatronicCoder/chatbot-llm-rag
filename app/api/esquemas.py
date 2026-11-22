"""Modelos de entrada y salida de la API (documentados en /docs)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

ID_PATRON = r"^[\w.@-]{1,60}$"


class PeticionChat(BaseModel):
    mensaje: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        examples=["un libro de fantasía para mi hija de 10 años"],
    )
    usuario_id: str = Field(default="cliente", pattern=ID_PATRON)
    session_id: str = Field(default="demo", pattern=ID_PATRON)


class HerramientaUsada(BaseModel):
    nombre: str
    argumentos: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    ms: int = 0
    resumen: str = ""


class RespuestaChatAPI(BaseModel):
    respuesta: str
    usuario_id: str
    session_id: str
    ruta: str = Field(
        "directa",
        description="faq, escalar, filtro, directa, herramientas, recomendacion, accion, "
        "rag o demo",
    )
    fuente: str = ""
    faq_id: str | None = None
    similitud: float | None = None
    herramientas: list[HerramientaUsada] = Field(default_factory=list)
    pedidos: list[str] = Field(default_factory=list)
    pagos: list[str] = Field(default_factory=list)
    documentos: list[dict[str, Any]] = Field(default_factory=list)
    libros: list[dict[str, Any]] = Field(
        default_factory=list, description="Libros mostrados en el turno (para tarjetas)"
    )
    modo: str = Field("real", description="real, sin_llm o demo")
    modelo: str = "—"
    endpoint: str = ""
    latencia_ms: int = 0
    tokens: list[int] = Field(default_factory=list)
    turno: int = 1
    turno_id: str = ""
    aviso: str | None = None
    clasificacion: dict[str, Any] | None = None


class LibroResumen(BaseModel):
    isbn: str
    titulo: str
    autor: str
    categoria: str
    genero: str | None = None
    edad: str | None = None
    formato: str | None = None
    precio: float
    stock: int
    valoracion: float | None = None
    num_valoraciones: int = 0
    sinopsis: str = ""
    portada: str | None = None


class ListaLibros(BaseModel):
    total: int
    libros: list[LibroResumen]


class LibroDetalle(BaseModel):
    isbn: str
    titulo: str
    autor: str
    editorial: str
    anio: int | None
    categoria: str
    genero: str
    temas: list[str]
    protagonista: str
    tono: str
    edad_min: int | None
    edad_max: int | None
    sinopsis: str
    formato: str
    paginas: int | None
    idioma: str
    valoracion: float | None
    num_valoraciones: int
    similares: list[str]
    precio: float
    stock: int
    portada: str | None


class Categoria(BaseModel):
    categoria: str
    libros: int


class Pedido(BaseModel):
    pedido_id: str
    usuario_id: str
    isbn: str
    titulo: str
    autor: str
    cantidad: int
    precio_unitario: float
    total: float
    estado: str
    creado_en: str


class ListaPedidos(BaseModel):
    usuario_id: str
    total: int
    pendiente: float
    pagado: float
    pedidos: list[Pedido]


class SesionBorrada(BaseModel):
    session_id: str
    borrados: int
