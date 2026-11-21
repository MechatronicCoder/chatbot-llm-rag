"""El turno completo de la conversación.

    mensaje
      ├─ 1. redacción de datos sensibles ............. evento dato.enmascarado
      ├─ 2. guardias (inyección / palabra prohibida) . ruta=filtro
      ├─ 3. clasificador de FAQs ..................... ruta=faq o escalar
      ├─ 4. LLM con herramientas ..................... ruta=directa, herramientas, accion
      │     └─ buscar_politica -> RAG con MMR ........ ruta=rag
      └─ 5. cierre: base de datos y evento turno.fin

El orden importa: las guardias y el clasificador deciden antes de que el modelo
vea el mensaje. Un saludo no debe arrastrar documentos al prompt y una pregunta
de envío ya respondida en las FAQs no debe llegar al modelo para que la improvise.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core import seguridad
from app.core.config import Settings
from app.core.logs import RegistroEventos, nuevo_turno
from app.llm.cliente import ClienteLLM, Mensaje, ModeloChat, SinProveedores
from app.llm.embeddings import ClienteEmbeddings
from app.persistencia.db import BaseDatos
from app.servicios.faq import Decision, IndiceFAQ
from app.servicios.herramientas import ACCIONES, IDEMPOTENTES, Herramientas
from app.servicios.rag import BuscadorDocumentos, ParametrosRAG

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
class _Acumulado:
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


class ServicioChat:
    """Un turno = guardias + clasificador + (modelo y herramientas) + cierre."""

    def __init__(
        self,
        *,
        bd: BaseDatos,
        registro: RegistroEventos,
        buscador: BuscadorDocumentos,
        llm: ModeloChat,
        faq: IndiceFAQ,
        prompt_sistema: str,
        ajustes: AjustesChat | None = None,
    ) -> None:
        self.bd = bd
        self.registro = registro
        self.buscador = buscador
        self.llm = llm
        self.faq = faq
        self.prompt_sistema = prompt_sistema.strip()
        self.ajustes = ajustes or AjustesChat()
        self.herramientas = Herramientas(bd, buscador)

    # -- memoria de la sesión (vive en la base de datos) -----------------------
    def historial(self, session_id: str) -> list[Mensaje]:
        filas = self.bd.mensajes_de(session_id, limite=self.ajustes.max_mensajes)
        return [
            {
                "role": "user" if f["rol"] == "user" else "assistant",
                "content": f["contenido_redactado"],
            }
            for f in filas
        ]

    # -- turno -----------------------------------------------------------------
    def responder(
        self, mensaje: str, usuario_id: str = "cliente", session_id: str = "demo"
    ) -> RespuestaChat:
        mensaje = (mensaje or "").strip()
        if not mensaje:
            raise ValueError("El mensaje no puede estar vacío.")
        if len(mensaje) > self.ajustes.limite_mensaje:
            raise ValueError(f"El mensaje supera los {self.ajustes.limite_mensaje} caracteres.")

        inicio = time.perf_counter()
        turno_id = nuevo_turno()
        self.bd.asegurar_usuario(usuario_id)
        self.registro.info(
            "turno.inicio", turno_id, sesion=session_id, usuario=usuario_id, largo=len(mensaje)
        )

        # 1. redacción: el valor original no llega ni a la base ni al log ni al modelo
        limpio, tipos = seguridad.redactar_datos_sensibles(mensaje)
        if tipos:
            self.registro.info("dato.enmascarado", turno_id, tipos=",".join(tipos))

        cierre = {"usuario_id": usuario_id, "session_id": session_id, "turno_id": turno_id}

        # 2. guardias
        motivo = seguridad.detectar_inyeccion(limpio)
        evento = "guardia.inyeccion"
        if motivo is None:
            motivo = seguridad.detectar_palabra_prohibida(limpio)
            evento = "guardia.palabra_prohibida"
        if motivo is not None:
            self.registro.aviso(evento, turno_id, patron=motivo)
            return self._cerrar(
                seguridad.respuesta_bloqueo(motivo),
                limpio,
                inicio,
                ruta="filtro",
                fuente="filtro de seguridad",
                modo="sin_llm",
                **cierre,
            )

        # 3. clasificador de FAQs (con guardia de acción)
        accion = RE_ACCION.search(limpio)
        decision = self.faq.clasificar(
            limpio,
            forzar_llm=bool(accion),
            motivo_forzado=f"peticion de accion: {accion.group(0)!r}" if accion else None,
        )
        self.registro.info(
            "clasificacion",
            turno_id,
            ruta=decision.ruta,
            sim=round(decision.similitud, 3),
            segunda=round(decision.similitud_segunda, 3),
            faq=decision.faq.id if decision.faq else "-",
        )
        if decision.ruta == "faq" and decision.faq is not None:
            self.registro.info("faq.hit", turno_id, faq=decision.faq.id)
            return self._cerrar(
                decision.faq.respuesta,
                limpio,
                inicio,
                ruta="faq",
                fuente=f"Base FAQ ({decision.faq.id})",
                faq_id=decision.faq.id,
                similitud=decision.similitud,
                modo="sin_llm",
                clasificacion=decision.to_dict(),
                **cierre,
            )
        if decision.ruta == "escalar":
            self.registro.aviso("escalado", turno_id, motivo=decision.motivo)
            return self._cerrar(
                MENSAJE_ESCALADO,
                limpio,
                inicio,
                ruta="escalar",
                fuente="asesor humano",
                similitud=decision.similitud,
                modo="sin_llm",
                clasificacion=decision.to_dict(),
                **cierre,
            )

        # 4. modelo con herramientas
        return self._con_modelo(limpio, inicio, decision, cierre)

    # -- camino con modelo -------------------------------------------------------
    def _mensajes_iniciales(self, mensaje: str, session_id: str, saludo: bool) -> list[Mensaje]:
        sistema = self.prompt_sistema
        if not saludo:
            contexto = self.faq.contexto(mensaje)
            if contexto:
                sistema += (
                    "\n\nPreguntas frecuentes verificadas que pueden servir "
                    f"(úsalas solo si responden la consulta):\n{contexto}"
                )
        return [
            {"role": "system", "content": sistema},
            *self.historial(session_id),
            {"role": "user", "content": mensaje},
        ]

    def _con_modelo(
        self, mensaje: str, inicio: float, decision: Decision, cierre: dict[str, str]
    ) -> RespuestaChat:
        turno_id, usuario_id = cierre["turno_id"], cierre["usuario_id"]
        saludo = bool(RE_SALUDO.match(mensaje))
        mensajes = self._mensajes_iniciales(mensaje, cierre["session_id"], saludo)
        acumulado = _Acumulado()
        a = self.ajustes

        try:
            for _ in range(a.max_vueltas):
                resp = self.llm.chat(
                    mensajes,
                    None if saludo else self.herramientas.esquemas(),
                    temperatura=a.temperatura,
                    max_tokens=a.max_tokens,
                )
                acumulado.sumar_uso(resp.uso)
                ms_llm = int(resp.latencia_s * 1000)
                self.registro.info(
                    "llm.llamada",
                    turno_id,
                    modelo=resp.modelo,
                    proveedor=resp.proveedor,
                    ms=ms_llm,
                    tool_calls=len(resp.tool_calls),
                )
                if ms_llm > a.llm_lento_ms:
                    self.registro.aviso("llm.lento", turno_id, ms=ms_llm, umbral=a.llm_lento_ms)

                if not resp.tool_calls:
                    texto, bloqueada, motivo = seguridad.validar_salida(
                        resp.texto.strip() or "No se pudo completar la gestión."
                    )
                    if bloqueada:
                        self.registro.aviso("salida.bloqueada", turno_id, motivo=motivo)
                    elif motivo:
                        self.registro.info("salida.redactada", turno_id, motivo=motivo)
                    return self._cerrar_con_modelo(
                        texto,
                        mensaje,
                        inicio,
                        decision,
                        cierre,
                        acumulado,
                        resp.modelo,
                        resp.proveedor,
                    )

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
                mensajes.append(asistente)

                for tc in resp.tool_calls:
                    resultado = self._ejecutar(tc, usuario_id, turno_id, acumulado)
                    mensajes.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(resultado, ensure_ascii=False, default=str),
                        }
                    )

            # se agotaron las vueltas: una última llamada sin herramientas para cerrar
            resp = self.llm.chat(mensajes, temperatura=a.temperatura, max_tokens=a.max_tokens)
            acumulado.sumar_uso(resp.uso)
            texto, _, _ = seguridad.validar_salida(
                resp.texto.strip() or "No se pudo completar la gestión."
            )
            return self._cerrar_con_modelo(
                texto, mensaje, inicio, decision, cierre, acumulado, resp.modelo, resp.proveedor
            )

        except SinProveedores as exc:
            aviso = str(exc).splitlines()[0]
            self.registro.aviso("degradacion", turno_id, motivo=aviso)
            return self._demo(mensaje, inicio, decision, cierre, acumulado, aviso)

    def _ejecutar(
        self, tc: dict[str, Any], usuario_id: str, turno_id: str, acumulado: _Acumulado
    ) -> dict[str, Any]:
        nombre = tc["name"]
        firma = nombre + json.dumps(tc["arguments"] or {}, sort_keys=True, ensure_ascii=False)
        if nombre in IDEMPOTENTES and firma in acumulado.cache:
            return acumulado.cache[firma]

        t0 = time.perf_counter()
        resultado = self.herramientas.ejecutar(nombre, tc["arguments"], usuario_id)
        ms = int((time.perf_counter() - t0) * 1000)
        if nombre in IDEMPOTENTES:
            acumulado.cache[firma] = resultado

        ok = "error" not in resultado
        resumen = _resumen(resultado)
        acumulado.usadas.append(
            {
                "nombre": nombre,
                "ok": ok,
                "ms": ms,
                "argumentos": tc["arguments"],
                "resumen": resumen,
            }
        )
        self.registro.info(
            "herramienta.ejecutada", turno_id, herramienta=nombre, ok=ok, ms=ms, resumen=resumen
        )
        if nombre == "buscar_politica" and resultado.get("hallazgos"):
            acumulado.documentos = resultado["hallazgos"]
            self.registro.info(
                "rag.recuperado",
                turno_id,
                chunks=len(acumulado.documentos),
                mejor=acumulado.documentos[0]["score"],
                motor=acumulado.documentos[0].get("motor"),
            )
        if nombre == "reservar_pedido" and resultado.get("pedido_id"):
            acumulado.pedidos.append(resultado["pedido_id"])
            self.registro.info(
                "pedido.creado",
                turno_id,
                pedido=resultado["pedido_id"],
                total=resultado.get("total"),
            )
        if nombre == "cobrar_pedido" and resultado.get("pago_id"):
            acumulado.pagos.append(resultado["pago_id"])
            self.registro.info(
                "pedido.cobrado",
                turno_id,
                pago=resultado["pago_id"],
                pedido=resultado.get("pedido_id"),
            )
        if nombre == "cancelar_pedido" and resultado.get("estado") == "cancelado":
            self.registro.info("pedido.cancelado", turno_id, pedido=resultado.get("pedido_id"))
        return resultado

    def _cerrar_con_modelo(
        self,
        texto: str,
        mensaje: str,
        inicio: float,
        decision: Decision,
        cierre: dict[str, str],
        acumulado: _Acumulado,
        modelo: str,
        proveedor: str,
    ) -> RespuestaChat:
        return self._cerrar(
            texto,
            mensaje,
            inicio,
            ruta=_ruta_de(acumulado.usadas),
            fuente=_fuente_de(acumulado.usadas),
            herramientas=acumulado.usadas,
            pedidos=acumulado.pedidos,
            pagos=acumulado.pagos,
            documentos=acumulado.documentos,
            modo="real",
            modelo=modelo,
            endpoint=proveedor,
            tokens=acumulado.tokens,
            clasificacion=decision.to_dict(),
            **cierre,
        )

    # -- modo demo: sin modelo, el producto sigue siendo útil --------------------
    def _demo(
        self,
        mensaje: str,
        inicio: float,
        decision: Decision,
        cierre: dict[str, str],
        acumulado: _Acumulado,
        aviso: str,
    ) -> RespuestaChat:
        """Sin modelo se responde solo con datos verificados: FAQ cercana o catálogo."""
        similitud: float | None = None
        if decision.similitud >= self.ajustes.faq_contexto_min:
            texto = decision.faq_similar.respuesta
            fuente = f"modo demo · FAQ cercana ({decision.faq_similar.id})"
            similitud = decision.similitud
        elif productos := self.bd.buscar_productos(mensaje, limite=3):
            listado = "; ".join(
                f"{p['nombre']} ({p['codigo']}) ${p['precio']:.2f} · {p['stock']} en stock"
                for p in productos
            )
            texto = f"Modo demo, sin modelo configurado. Del catálogo se confirma: {listado}."
            fuente = "modo demo · catálogo"
        else:
            texto = (
                "Modo demo, sin modelo configurado: se puede consultar el catálogo y los "
                "pedidos, pero no redactar respuestas libres."
            )
            fuente = "modo demo"
        return self._cerrar(
            texto,
            mensaje,
            inicio,
            ruta="demo",
            fuente=fuente,
            similitud=similitud,
            herramientas=acumulado.usadas,
            pedidos=acumulado.pedidos,
            pagos=acumulado.pagos,
            modo="demo",
            tokens=acumulado.tokens,
            aviso=aviso,
            clasificacion=decision.to_dict(),
            **cierre,
        )

    # -- cierre ------------------------------------------------------------------
    def _cerrar(self, texto: str, mensaje: str, inicio: float, **datos: Any) -> RespuestaChat:
        latencia = int((time.perf_counter() - inicio) * 1000)
        tokens = list(datos.pop("tokens", None) or [0, 0])
        session_id = datos["session_id"]
        self.bd.guardar_turno(
            datos["turno_id"],
            session_id,
            datos["usuario_id"],
            mensaje,
            texto,
            ruta=datos.get("ruta", ""),
            fuente=datos.get("fuente", ""),
            faq_id=datos.get("faq_id"),
            similitud=datos.get("similitud"),
            tokens=(tokens[0], tokens[1]),
            latencia_ms=latencia,
            herramientas=datos.get("herramientas") or [],
        )
        self.registro.info(
            "turno.fin",
            datos["turno_id"],
            ruta=datos.get("ruta", ""),
            ms=latencia,
            tokens_entrada=tokens[0],
            tokens_salida=tokens[1],
            herramientas=len(datos.get("herramientas") or []),
        )
        return RespuestaChat(
            respuesta=texto,
            latencia_ms=latencia,
            turno=self.bd.contar_turnos(session_id),
            tokens=tokens,
            **datos,
        )


def _ruta_de(usadas: list[dict[str, Any]]) -> str:
    nombres = {u["nombre"] for u in usadas}
    if "buscar_politica" in nombres:
        return "rag"
    if nombres & ACCIONES:
        return "accion"
    return "herramientas" if nombres else "directa"


def _fuente_de(usadas: list[dict[str, Any]]) -> str:
    nombres = list(dict.fromkeys(u["nombre"] for u in usadas))
    return "LLM + " + ", ".join(nombres) if nombres else "LLM (sin herramientas)"


def _resumen(resultado: dict[str, Any]) -> str:
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


# ---------------------------------------------------------------------------
# Fábrica: arma el servicio real a partir de la configuración
# ---------------------------------------------------------------------------
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
