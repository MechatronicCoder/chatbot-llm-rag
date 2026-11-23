"""Verificación de la respuesta del modelo antes de entregarla al cliente.

El prompt le pide al modelo no inventar, pero un modelo real a veces confirma una
reserva que no hizo ("He reservado… pedido PED-9F3G7H2K") o cita una página de una
política que no consultó. Estas comprobaciones son deterministas: comparan lo que
dice la respuesta con lo que pasó de verdad en la base y en el turno.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.persistencia.db import BaseDatos
from app.servicios.turno import RE_BUSQUEDA, RE_EDAD, Acumulado

RE_ID = re.compile(r"\b(?:PED|PAG)-[0-9A-Z]{4,16}\b", re.IGNORECASE)
# "página 2", "pág. 3", "secc. 4", 'sección "Libros con defectos"', "según nuestra política"
RE_CITA = re.compile(
    r"\bp[aá]g(?:ina|\.)\s*\d"
    r"|\bsecc(?:i[oó]n|\.)\s*(?:\d|[\"“«'])"
    r"|\bseg[uú]n (?:nuestra|la) pol[ií]tica",
    re.IGNORECASE,
)
# "pagado", "pago confirmado", "procesé el pago"... y "cancelado"
RE_PAGADO = re.compile(
    r"\b(?:pagad[oa]s?|cobrad[oa]s?"
    r"|pago (?:confirmado|procesado|realizado|registrado|aprobado|exitoso)"
    r"|(?:proces|realic|registr)[eé] (?:el|tu) (?:pago|cobro))\b",
    re.IGNORECASE,
)
RE_CANCELADO = re.compile(r"\bcancelad[oa]s?\b", re.IGNORECASE)
# "algo de terror…", "algún libro…": pedidos que RE_BUSQUEDA no cubre
RE_ALGO = re.compile(r"\b(?:algo|alg[uú]n)\b", re.IGNORECASE)
NOTA = "[Revisión interna; no la menciones al cliente] "


@dataclass(frozen=True)
class Problema:
    motivo: str  # para el log
    instruccion: str  # lo que se le pide al modelo para corregir
    respaldo: str  # lo que ve el cliente si el modelo no corrige


def _id_real(ident: str, acumulado: Acumulado, bd: BaseDatos, usuario_id: str) -> bool:
    if ident in acumulado.pedidos or ident in acumulado.pagos:
        return True
    pedidos = bd.pedidos_de(usuario_id)
    if ident.startswith("PED-"):
        return any(p["pedido_id"] == ident for p in pedidos)
    return any(pago["id"] == ident for p in pedidos for pago in bd.pagos_de(p["pedido_id"]))


def revisar_respuesta(
    texto: str, acumulado: Acumulado, bd: BaseDatos, usuario_id: str, mensaje: str = ""
) -> Problema | None:
    """El primer problema encontrado, o None si la respuesta se puede entregar."""
    # "algo de terror para mi hijo de 10 años": ya dijo qué busca y la edad, así que
    # una pregunta sin haber recomendado nada es una vuelta de más para el cliente.
    pide = RE_BUSQUEDA.search(mensaje) or RE_ALGO.search(mensaje)
    pide_con_edad = bool(pide and RE_EDAD.search(mensaje))
    if pide_con_edad and not acumulado.usadas and "?" in texto:
        return Problema(
            motivo="pregunta en lugar de recomendar",
            instruccion=NOTA + "El cliente ya dijo qué busca y la edad del lector: llama ahora a "
            "recomendar_libros con eso y recomienda; después puedes ofrecer afinar.",
            respaldo=texto.strip(),
        )
    ids = sorted({m.group(0).upper() for m in RE_ID.finditer(texto)})
    inventados = [i for i in ids if not _id_real(i, acumulado, bd, usuario_id)]
    if inventados:
        return Problema(
            motivo=f"id inexistente: {', '.join(inventados)}",
            instruccion=NOTA
            + f"{', '.join(inventados)} no existe: no se reservó ni se cobró nada. Si el "
            "cliente pidió reservar o pagar, llama ahora a la herramienta que corresponde y "
            "usa solo los ids que devuelva.",
            respaldo="No pude confirmar esa operación. ¿Me indicas el libro y cuántos "
            "ejemplares quieres para reservarlo?",
        )
    usadas = {u["nombre"] for u in acumulado.usadas if u.get("ok", True)}
    estados = {(bd.consultar_pedido(i) or {}).get("estado") for i in ids if i.startswith("PED-")}
    if RE_PAGADO.search(texto) and "cobrar_pedido" not in usadas and "pagado" not in estados:
        return Problema(
            motivo="afirma un pago que no se registró",
            instruccion=NOTA
            + "El pago no se registró: no llamaste a cobrar_pedido. Si el cliente pidió "
            "pagar, llámala ahora con el id exacto del pedido.",
            respaldo="Todavía no registré el pago. ¿Lo pago con tarjeta, efectivo o transferencia?",
        )
    if (
        RE_CANCELADO.search(texto)
        and "cancelar_pedido" not in usadas
        and "cancelado" not in estados
    ):
        return Problema(
            motivo="afirma una cancelación que no se registró",
            instruccion=NOTA
            + "No se canceló nada: no llamaste a cancelar_pedido. Si el cliente pidió "
            "cancelar, llámala ahora con el id exacto del pedido.",
            respaldo="Todavía no cancelé el pedido. ¿Me confirmas cuál quieres cancelar?",
        )
    if RE_CITA.search(texto) and "buscar_politica" not in usadas:
        return Problema(
            motivo="cita de una política sin consultar buscar_politica",
            instruccion=NOTA
            + "Citaste una política sin consultarla. Llama a buscar_politica y responde "
            "solo con lo que devuelva, citando el documento y la sección o página.",
            respaldo="Para darte la condición exacta tengo que revisar nuestras políticas. "
            "¿Me repites la consulta?",
        )
    return None
