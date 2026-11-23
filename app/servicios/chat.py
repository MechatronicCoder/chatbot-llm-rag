"""El turno completo de la conversación.

    mensaje
      ├─ 1. redacción de datos sensibles ............. evento dato.enmascarado
      ├─ 2. guardias (inyección / palabra prohibida) . ruta=filtro
      ├─ 3. clasificador de FAQs ..................... ruta=faq o escalar
      ├─ 4. LLM con herramientas ..................... ruta=directa, herramientas, accion
      │     ├─ recomendar_libros -> híbrido .......... ruta=recomendacion
      │     └─ buscar_politica -> RAG con MMR ........ ruta=rag
      └─ 5. cierre: base de datos y evento turno.fin

El orden importa: las guardias y el clasificador deciden antes de que el modelo
vea el mensaje. Un saludo no debe arrastrar documentos al prompt y una pregunta
de envío ya respondida en las FAQs no debe llegar al modelo para que la improvise.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.catalogo.carga import Tienda
from app.core import seguridad
from app.core.logs import RegistroEventos, nuevo_turno
from app.llm.cliente import ModeloChat, SinProveedores
from app.persistencia.db import BaseDatos
from app.servicios.contexto import CandadosPorSesion, mensajes_iniciales
from app.servicios.faq import Decision, IndiceFAQ
from app.servicios.herramientas import Herramientas
from app.servicios.rag import BuscadorDocumentos
from app.servicios.recomendador import Recomendador
from app.servicios.resumen import Resumidor
from app.servicios.turno import (
    MENSAJE_ESCALADO,
    RE_ACCION,
    RE_BUSQUEDA,
    RE_SALUDO,
    Acumulado,
    AjustesChat,
    RespuestaChat,
    ejecutar_llamada,
    es_seguimiento,
    fuente_de,
    mensaje_con_llamadas,
    referencias_del_turno,
    ruta_de,
    texto_demo,
)
from app.servicios.verificacion import revisar_respuesta


class ServicioChat:
    """Un turno = guardias + clasificador + (modelo y herramientas) + cierre."""

    def __init__(
        self,
        *,
        bd: BaseDatos,
        registro: RegistroEventos,
        buscador: BuscadorDocumentos,
        recomendador: Recomendador,
        llm: ModeloChat,
        faq: IndiceFAQ,
        prompt_sistema: str,
        tienda: Tienda,
        ajustes: AjustesChat | None = None,
        resumidor: Resumidor | None = None,
    ) -> None:
        self.bd = bd
        self.registro = registro
        self.buscador = buscador
        self.recomendador = recomendador
        self.llm = llm
        self.faq = faq
        self.prompt_sistema = prompt_sistema.strip()
        self.tienda = tienda
        self.ajustes = ajustes or AjustesChat()
        self.herramientas = Herramientas(bd, buscador, recomendador)
        self.candados = CandadosPorSesion()
        self.resumidor = resumidor  # resume lo que sale de la ventana del historial

    # -- turno -----------------------------------------------------------------
    def responder(
        self, mensaje: str, usuario_id: str = "cliente", session_id: str = "demo"
    ) -> RespuestaChat:
        # Dos mensajes de la misma conversación se atienden en orden, nunca a la vez.
        with self.candados.de(session_id):
            respuesta = self._responder(mensaje, usuario_id, session_id)
        if self.resumidor is not None:
            self.resumidor.programar(session_id)
        return respuesta

    def _responder(self, mensaje: str, usuario_id: str, session_id: str) -> RespuestaChat:
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
        motivo_forzado = None
        if accion := RE_ACCION.search(limpio):
            motivo_forzado = f"peticion de accion: {accion.group(0)!r}"
        elif busqueda := RE_BUSQUEDA.search(limpio):
            motivo_forzado = f"busqueda de libros: {busqueda.group(0)!r}"
        elif es_seguimiento(limpio):  # "¿y para 12 años?" depende del historial
            motivo_forzado = "seguimiento del mensaje anterior"
        decision = self.faq.clasificar(
            limpio, forzar_llm=motivo_forzado is not None, motivo_forzado=motivo_forzado
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
    def _con_modelo(
        self, mensaje: str, inicio: float, decision: Decision, cierre: dict[str, str]
    ) -> RespuestaChat:
        turno_id, usuario_id = cierre["turno_id"], cierre["usuario_id"]
        saludo = bool(RE_SALUDO.match(mensaje))
        mensajes = mensajes_iniciales(
            self.prompt_sistema,
            self.faq,
            self.bd,
            mensaje,
            cierre["session_id"],
            saludo=saludo,
            limite=self.ajustes.max_mensajes,
            faq_minimo=self.ajustes.faq_prompt_min,
        )
        acumulado = Acumulado()
        a = self.ajustes
        corregido = False

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
                    # Un id de pedido inventado o una política citada sin consultarla no se
                    # entregan: el modelo recibe una corrección y tiene una vuelta más.
                    problema = revisar_respuesta(
                        resp.texto, acumulado, self.bd, usuario_id, mensaje
                    )
                    if problema and not corregido:
                        corregido = True
                        self.registro.aviso(
                            "salida.no_verificada", turno_id, motivo=problema.motivo
                        )
                        mensajes.append({"role": "assistant", "content": resp.texto})
                        mensajes.append({"role": "user", "content": problema.instruccion})
                        continue
                    texto, bloqueada, motivo = seguridad.validar_salida(
                        (problema.respaldo if problema else resp.texto.strip())
                        or "No se pudo completar la gestión."
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

                mensajes.append(mensaje_con_llamadas(resp))

                for tc in resp.tool_calls:
                    resultado = ejecutar_llamada(
                        self.herramientas, self.registro, tc, usuario_id, turno_id, acumulado
                    )
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
            problema = revisar_respuesta(resp.texto, acumulado, self.bd, usuario_id, mensaje)
            texto, _, _ = seguridad.validar_salida(
                (problema.respaldo if problema else resp.texto.strip())
                or "No se pudo completar la gestión."
            )
            return self._cerrar_con_modelo(
                texto, mensaje, inicio, decision, cierre, acumulado, resp.modelo, resp.proveedor
            )

        except SinProveedores as exc:
            aviso = str(exc).splitlines()[0]
            self.registro.aviso("degradacion", turno_id, motivo=aviso)
            return self._demo(mensaje, inicio, decision, cierre, acumulado, aviso)

    def _cerrar_con_modelo(
        self,
        texto: str,
        mensaje: str,
        inicio: float,
        decision: Decision,
        cierre: dict[str, str],
        acumulado: Acumulado,
        modelo: str,
        proveedor: str,
    ) -> RespuestaChat:
        return self._cerrar(
            texto,
            mensaje,
            inicio,
            ruta=ruta_de(acumulado.usadas),
            fuente=fuente_de(acumulado.usadas),
            herramientas=acumulado.usadas,
            pedidos=acumulado.pedidos,
            pagos=acumulado.pagos,
            documentos=acumulado.documentos,
            libros=acumulado.libros,
            carrito=acumulado.carrito,
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
        acumulado: Acumulado,
        aviso: str,
    ) -> RespuestaChat:
        """Sin modelo se responde solo con datos verificados: FAQ cercana o catálogo."""
        texto, fuente, similitud, libros = texto_demo(
            self.bd,
            self.recomendador,
            decision,
            mensaje,
            self.ajustes.faq_contexto_min,
            self.tienda,
        )
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
            libros=libros,
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
        referencias = referencias_del_turno(datos.get("libros") or [], datos.get("pedidos") or [])
        self.bd.guardar_turno(
            datos["turno_id"],
            session_id,
            datos["usuario_id"],
            mensaje,
            texto + referencias,
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
