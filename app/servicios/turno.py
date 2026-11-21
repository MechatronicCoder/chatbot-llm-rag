"""Piezas del turno de conversación que no dependen del orden de los pasos.

Patrones de saludo y de acción, ajustes, la respuesta que devuelve la API, lo
que se acumula durante las vueltas con el modelo, la ejecución de herramientas
con sus eventos y la respuesta del modo demo.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.config import Settings
from app.core.logs import RegistroEventos
from app.persistencia.db import BaseDatos
from app.servicios.faq import Decision
from app.servicios.herramientas import ACCIONES, IDEMPOTENTES, Herramientas

# Uno o varios saludos o cortesías seguidos ("hola, buenas tardes", "gracias, eso era todo").
RE_SALUDO = re.compile(
    r"^[\s¿¡]*(?:(?:hola|buenas tardes|buenas noches|buenos d[ií]as|buenas|hey|qu[eé] tal|"
    r"saludos|muchas gracias|gracias|adi[oó]s|hasta luego|chao|c[oó]mo est[aá]s?|c[oó]mo va|"
    r"eso era todo)\b[\s!.,?¿¡]*)+$",
    re.IGNORECASE,
)

# Una acción sobre un pedido ("págala", "cancélalo", "PED-1A2B") no la responde
# la FAQ: la decide el modelo con las herramientas, que es quien ve el historial.
RE_ACCION = re.compile(
    r"\b(paga|págala|pagala|págalo|pagalo|cobra|cóbrala|cobrala|reserva|resérvala|"
    r"reservala|cancela|cancélalo|cancelalo|compra|cómprala|comprala)\b|\bPED-[A-Z0-9]{4,}\b",
    re.IGNORECASE,
)

MENSAJE_ESCALADO = (
    "Esa consulta la responde mejor un asesor humano; queda registrada. "
    "Mientras tanto se puede revisar el catálogo y el estado de los pedidos."
)


@dataclass(frozen=True)
class AjustesChat:
    max_mensajes: int = 8
    max_vueltas: int = 3
    limite_mensaje: int = 2000
    faq_contexto_min: float = 0.25
    temperatura: float = 0.3
    max_tokens: int = 700
    llm_lento_ms: int = 4000

    @classmethod
    def desde_config(cls, ajustes: Settings) -> AjustesChat:
        return cls(
            max_mensajes=ajustes.max_mensajes,
            max_vueltas=ajustes.max_vueltas,
            limite_mensaje=ajustes.limite_mensaje,
            faq_contexto_min=ajustes.faq_contexto_min,
            temperatura=ajustes.llm_temperatura,
            max_tokens=ajustes.llm_max_tokens,
            llm_lento_ms=ajustes.llm_lento_ms,
        )


@dataclass
class RespuestaChat:
    respuesta: str
    usuario_id: str
    session_id: str
    ruta: str = "directa"
    fuente: str = ""
    faq_id: str | None = None
    similitud: float | None = None
    herramientas: list[dict[str, Any]] = field(default_factory=list)
    pedidos: list[str] = field(default_factory=list)
    pagos: list[str] = field(default_factory=list)
    documentos: list[dict[str, Any]] = field(default_factory=list)
    modo: str = "real"
    modelo: str = "—"
    endpoint: str = ""
    latencia_ms: int = 0
    tokens: list[int] = field(default_factory=lambda: [0, 0])
    turno: int = 1
    turno_id: str = ""
    aviso: str | None = None
    clasificacion: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Acumulado:
    """Lo que se va juntando durante las vueltas con el modelo."""

    usadas: list[dict[str, Any]] = field(default_factory=list)
    pedidos: list[str] = field(default_factory=list)
    pagos: list[str] = field(default_factory=list)
    documentos: list[dict[str, Any]] = field(default_factory=list)
    tokens: list[int] = field(default_factory=lambda: [0, 0])
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def sumar_uso(self, uso: dict[str, int]) -> None:
        self.tokens[0] += uso.get("prompt_tokens", 0)
        self.tokens[1] += uso.get("completion_tokens", 0)


def ruta_de(usadas: list[dict[str, Any]]) -> str:
    nombres = {u["nombre"] for u in usadas}
    if "buscar_politica" in nombres:
        return "rag"
    if nombres & ACCIONES:
        return "accion"
    return "herramientas" if nombres else "directa"


def fuente_de(usadas: list[dict[str, Any]]) -> str:
    nombres = list(dict.fromkeys(u["nombre"] for u in usadas))
    return "LLM + " + ", ".join(nombres) if nombres else "LLM (sin herramientas)"


def resumir(resultado: dict[str, Any]) -> str:
    if "error" in resultado:
        return f"error: {str(resultado['error'])[:60]}"
    if "hallazgos" in resultado:
        mejor = max((h["score"] for h in resultado["hallazgos"]), default=0)
        return f"{len(resultado['hallazgos'])} fragmentos · mejor {mejor:.3f}"
    if "encontrados" in resultado:
        return f"{resultado['encontrados']} productos"
    if "pago_id" in resultado:
        return f"pago {resultado['pago_id']} · {resultado.get('monto')}"
    if "pedido_id" in resultado:
        return f"pedido {resultado['pedido_id']} · {resultado.get('estado')}"
    return ", ".join(resultado)[:60]


def ejecutar_llamada(
    herramientas: Herramientas,
    registro: RegistroEventos,
    tc: dict[str, Any],
    usuario_id: str,
    turno_id: str,
    acumulado: Acumulado,
) -> dict[str, Any]:
    """Ejecuta una llamada del modelo, la reutiliza si es idempotente y registra sus eventos."""
    nombre = tc["name"]
    firma = nombre + json.dumps(tc["arguments"] or {}, sort_keys=True, ensure_ascii=False)
    if nombre in IDEMPOTENTES and firma in acumulado.cache:
        return acumulado.cache[firma]

    t0 = time.perf_counter()
    resultado = herramientas.ejecutar(nombre, tc["arguments"], usuario_id)
    ms = int((time.perf_counter() - t0) * 1000)
    if nombre in IDEMPOTENTES:
        acumulado.cache[firma] = resultado

    ok = "error" not in resultado
    resumen = resumir(resultado)
    acumulado.usadas.append(
        {
            "nombre": nombre,
            "ok": ok,
            "ms": ms,
            "argumentos": tc["arguments"],
            "resumen": resumen,
        }
    )
    registro.info(
        "herramienta.ejecutada", turno_id, herramienta=nombre, ok=ok, ms=ms, resumen=resumen
    )
    if nombre == "buscar_politica" and resultado.get("hallazgos"):
        acumulado.documentos = resultado["hallazgos"]
        registro.info(
            "rag.recuperado",
            turno_id,
            chunks=len(acumulado.documentos),
            mejor=acumulado.documentos[0]["score"],
            motor=acumulado.documentos[0].get("motor"),
        )
    if nombre == "reservar_pedido" and resultado.get("pedido_id"):
        acumulado.pedidos.append(resultado["pedido_id"])
        registro.info(
            "pedido.creado",
            turno_id,
            pedido=resultado["pedido_id"],
            total=resultado.get("total"),
        )
    if nombre == "cobrar_pedido" and resultado.get("pago_id"):
        acumulado.pagos.append(resultado["pago_id"])
        registro.info(
            "pedido.cobrado",
            turno_id,
            pago=resultado["pago_id"],
            pedido=resultado.get("pedido_id"),
        )
    if nombre == "cancelar_pedido" and resultado.get("estado") == "cancelado":
        registro.info("pedido.cancelado", turno_id, pedido=resultado.get("pedido_id"))
    return resultado


def texto_demo(
    bd: BaseDatos, decision: Decision, mensaje: str, minimo_faq: float
) -> tuple[str, str, float | None]:
    """Sin modelo se responde solo con datos verificados: FAQ cercana o catálogo.

    Devuelve `(texto, fuente, similitud)`.
    """
    if decision.similitud >= minimo_faq:
        return (
            decision.faq_similar.respuesta,
            f"modo demo · FAQ cercana ({decision.faq_similar.id})",
            decision.similitud,
        )
    if productos := bd.buscar_productos(mensaje, limite=3):
        listado = "; ".join(
            f"{p['nombre']} ({p['codigo']}) ${p['precio']:.2f} · {p['stock']} en stock"
            for p in productos
        )
        texto = f"Modo demo, sin modelo configurado. Del catálogo se confirma: {listado}."
        return texto, "modo demo · catálogo", None
    texto = (
        "Modo demo, sin modelo configurado: se puede consultar el catálogo y los "
        "pedidos, pero no redactar respuestas libres."
    )
    return texto, "modo demo", None
