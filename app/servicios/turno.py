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

from app.catalogo.carga import Tienda
from app.core.config import Settings
from app.core.isbn import RE_ISBN, isbns_en
from app.core.logs import RegistroEventos
from app.llm.cliente import Mensaje, RespuestaLLM
from app.persistencia.db import BaseDatos
from app.servicios.faq import Decision
from app.servicios.herramientas import ACCIONES, IDEMPOTENTES, Herramientas
from app.servicios.recomendador import Recomendador, resumen_libro

MAX_LIBROS_POR_TURNO = 6

# Uno o varios saludos o cortesías seguidos ("hola, buenas tardes", "gracias, eso era todo").
RE_SALUDO = re.compile(
    r"^[\s¿¡]*(?:(?:hola|buenas tardes|buenas noches|buenos d[ií]as|buenas|hey|qu[eé] tal|"
    r"saludos|muchas gracias|gracias|adi[oó]s|hasta luego|chao|c[oó]mo est[aá]s?|c[oó]mo va|"
    r"eso era todo)\b[\s!.,?¿¡]*)+$",
    re.IGNORECASE,
)

# Una acción sobre un pedido ("págalo", "lo quiero", "cancélalo", "PED-1A2B") no
# la responde la FAQ: la decide el modelo con las herramientas, que ve el historial.
RE_ACCION = re.compile(
    r"\b(paga|págala|pagala|págalo|pagalo|cobra|cóbrala|cobrala|cóbralo|cobralo|reserva|"
    r"resérvala|reservala|resérvalo|reservalo|cancela|cancélalo|cancelalo|compra|cómprala|"
    r"comprala|cómpralo|compralo|apártalo|apartalo|me lo llevo|me la llevo|lo quiero|"
    r"la quiero|los quiero|quiero comprar|quiero reservar|quiero pedir|quiero el libro|"
    r"quiero ese libro|quiero este libro|agrega|agrégalo|agregalo|añade|añádelo|añadelo|quita|"
    r"quítalo|quitalo)\b|\bPED-[A-Z0-9]{4,}\b|"
    # el carrito o el pedido propio: la FAQ no los conoce ("¿cuánto me falta para el envío gratis?")
    r"\b(?:mi|el) (?:carrito|pedido)\b|\bme falta\b|" + RE_ISBN.pattern,
    # un ISBN-13 (con o sin guiones) es un libro concreto: lo resuelven las herramientas
    re.IGNORECASE,
)

# Buscar o pedir recomendación de un libro ("un libro para…", "libros de fantasía",
# "recomiéndame", "busco") no lo responde la FAQ aunque se parezca a "quiero devolver
# un libro": lo resuelven las herramientas. "Libro de reclamaciones" sí es FAQ.
RE_BUSQUEDA = re.compile(
    r"\b(busco|buscando|recomi[eé]nda(me|s)?|recomiendas|sug[ie][eé]re(me)?|sugieres)\b|"
    r"\b(un|una|algún|algun|algo)\s+(libro|novela|cuento|cómic|comic)\b"
    r"(?!\s+de\s+reclamaciones)\s*(para|de|sobre|que|como|parecid[oa]|similar|con)\b|"
    r"\blibros\s+(de|sobre|para(?!\s+regalo)|como|parecidos|similares|con)\b|"
    r"\b(parecid[oa]s?|similar(es)?)\s+a\b",
    re.IGNORECASE,
)

# "para mi hijo de 5 años" -> 5 (solo para el modo demo; con modelo, lo deduce el LLM)
RE_EDAD = re.compile(r"\b(\d{1,2})\s*años\b", re.IGNORECASE)

# Confirmación fija: el cliente ve el total que calculó el sistema antes de que se cobre.
AVISO_COBRO = (
    "No se cobró: el total de este pedido cambió en este mismo turno y el cliente todavía no "
    "lo vio. Muéstrale el resumen y pregúntale si confirma el pago."
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
    libros: list[dict[str, Any]] = field(default_factory=list)
    modo: str = "real"
    modelo: str = "—"
    endpoint: str = ""
    latencia_ms: int = 0
    tokens: list[int] = field(default_factory=lambda: [0, 0])
    turno: int = 1
    turno_id: str = ""
    aviso: str | None = None
    clasificacion: dict[str, Any] | None = None
    carrito: dict[str, Any] | None = None  # el último pedido o carrito que tocó el turno

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Acumulado:
    """Lo que se va juntando durante las vueltas con el modelo."""

    usadas: list[dict[str, Any]] = field(default_factory=list)
    pedidos: list[str] = field(default_factory=list)
    pagos: list[str] = field(default_factory=list)
    documentos: list[dict[str, Any]] = field(default_factory=list)
    libros: list[dict[str, Any]] = field(default_factory=list)
    tokens: list[int] = field(default_factory=lambda: [0, 0])
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    cotizados: set[str] = field(default_factory=set)  # pedidos cuyo total cambió en el turno
    carrito: dict[str, Any] | None = None

    def sumar_uso(self, uso: dict[str, int]) -> None:
        self.tokens[0] += uso.get("prompt_tokens", 0)
        self.tokens[1] += uso.get("completion_tokens", 0)

    def agregar_libros(self, libros: list[dict[str, Any]]) -> None:
        """Libros mostrados en el turno (para tarjetas en el frontend), sin repetir."""
        vistos = {lb["isbn"] for lb in self.libros}
        for libro in libros:
            if libro["isbn"] not in vistos and len(self.libros) < MAX_LIBROS_POR_TURNO:
                self.libros.append(libro)
                vistos.add(libro["isbn"])


def ruta_de(usadas: list[dict[str, Any]]) -> str:
    nombres = {u["nombre"] for u in usadas}
    if "buscar_politica" in nombres:
        return "rag"
    if nombres & ACCIONES:
        return "accion"
    if "recomendar_libros" in nombres:
        return "recomendacion"
    return "herramientas" if nombres else "directa"


def mensaje_con_llamadas(resp: RespuestaLLM) -> Mensaje:
    """Turno del asistente con sus llamadas, en el formato que el modelo espera de vuelta."""
    asistente: Mensaje = {
        "role": "assistant",
        "content": resp.texto or "",
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            }
            for tc in resp.tool_calls
        ],
    }
    if resp.razonamiento:  # algunos modelos con razonamiento lo exigen de vuelta
        asistente["reasoning_content"] = resp.razonamiento
    return asistente


def referencias_del_turno(libros: list[dict[str, Any]], pedidos: list[str]) -> str:
    """Nota que se guarda al final de la respuesta en el historial; el cliente no la ve.

    El historial guarda solo texto, así que sin esta nota el modelo no ve en el
    turno siguiente los ISBN ni los pedidos que devolvieron las herramientas y
    tiende a inventarlos ("me interesa el primero", "págalo").
    """
    partes = [f"ISBN {lb['isbn']} «{lb['titulo']}»" for lb in libros]
    partes += [f"pedido {p}" for p in pedidos]
    return f"\n\n[Referencias del turno: {'; '.join(partes)}]" if partes else ""


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
        return f"{resultado['encontrados']} libros"
    if "recomendaciones" in resultado:
        return f"{len(resultado['recomendaciones'])} recomendaciones"
    if "similares_disponibles" in resultado:
        return f"ficha {resultado['isbn']}"
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
    pedido_id = str((tc["arguments"] or {}).get("pedido_id", "")).upper()
    if nombre == "cobrar_pedido" and pedido_id in acumulado.cotizados:
        resultado: dict[str, Any] = {"error": AVISO_COBRO}
    else:
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
    if nombre == "buscar_libros" and resultado.get("libros"):
        acumulado.agregar_libros(resultado["libros"])
    if nombre == "recomendar_libros" and resultado.get("recomendaciones"):
        acumulado.agregar_libros(resultado["recomendaciones"])
        registro.info(
            "recomendacion.generada",
            turno_id,
            isbn=",".join(r["isbn"] for r in resultado["recomendaciones"]),
            motor=resultado["recomendaciones"][0]["motor"],
        )
    if nombre == "ficha_libro" and resultado.get("isbn"):
        acumulado.agregar_libros([resumen_libro(resultado)])
    if nombre == "buscar_politica" and resultado.get("hallazgos"):
        acumulado.documentos = resultado["hallazgos"]
        registro.info(
            "rag.recuperado",
            turno_id,
            chunks=len(acumulado.documentos),
            mejor=acumulado.documentos[0]["score"],
            motor=acumulado.documentos[0].get("motor"),
        )
    if "lineas" in resultado:  # cualquier herramienta de pedidos devuelve el pedido completo
        acumulado.carrito = resultado
    if nombre in ("actualizar_carrito", "reservar_pedido") and ok:
        if resultado["pedido_id"] not in acumulado.pedidos:
            acumulado.pedidos.append(resultado["pedido_id"])
        if nombre == "actualizar_carrito":
            acumulado.cotizados.add(resultado["pedido_id"])
        registro.info(
            "carrito.actualizado" if nombre == "actualizar_carrito" else "pedido.creado",
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


def libros_para_demo(
    bd: BaseDatos, recomendador: Recomendador, mensaje: str
) -> list[dict[str, Any]]:
    """Un ISBN se busca tal cual; lo demás pasa por el recomendador por contenido."""
    if isbns_en(mensaje):
        return [resumen_libro(lb) for lb in bd.buscar_libros(mensaje, limite=3)]
    edad = int(coincidencia.group(1)) if (coincidencia := RE_EDAD.search(mensaje)) else None
    vivos = {lb["isbn"]: lb for lb in bd.libros_activos()}
    return recomendador.recomendar(mensaje, vivos, edad=edad, cantidad=3)


def texto_demo(
    bd: BaseDatos,
    recomendador: Recomendador,
    decision: Decision,
    mensaje: str,
    minimo_faq: float,
    tienda: Tienda,
) -> tuple[str, str, float | None, list[dict[str, Any]]]:
    """Sin modelo se responde solo con datos verificados: catálogo o FAQ cercana.

    Primero el catálogo: "busco Harry Potter" debe encontrar el libro antes que
    parecerse a la FAQ de pedidos especiales. Devuelve `(texto, fuente, similitud,
    libros)`; los libros se muestran como tarjetas en el frontend.
    """
    if libros := libros_para_demo(bd, recomendador, mensaje):
        listado = "; ".join(
            f"«{lb['titulo']}» de {lb['autor']}, {tienda.precio(lb['precio'])} · "
            f"{lb['stock']} en stock"
            for lb in libros
        )
        texto = f"Modo demo, sin modelo configurado. Del catálogo se confirma: {listado}."
        return texto, "modo demo · catálogo", None, libros
    if decision.similitud >= minimo_faq:
        return (
            decision.faq_similar.respuesta,
            f"modo demo · FAQ cercana ({decision.faq_similar.id})",
            decision.similitud,
            [],
        )
    texto = (
        "Modo demo, sin modelo configurado: se puede consultar el catálogo y los "
        "pedidos, pero no redactar respuestas libres."
    )
    return texto, "modo demo", None, []
