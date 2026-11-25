"""Verificación de la respuesta del modelo antes de entregarla al cliente.

El prompt le pide al modelo no inventar, pero un modelo real a veces confirma una
reserva que no hizo ("He reservado… pedido PED-9F3G7H2K"), cita una página de una
política que no consultó o suma un total por su cuenta. Estas comprobaciones son
deterministas: comparan lo que dice la respuesta con lo que pasó de verdad en la
base y en el turno.
"""

from __future__ import annotations

import re
from typing import Any

from app.catalogo.cotizacion import a_centimos
from app.core.texto import normalizar
from app.persistencia.db import BaseDatos
from app.servicios.lectura import (
    RE_DESTACADO,
    RE_ID,
    RE_MONTO,
    RE_MONTO_COMPLETO,
    RE_NEGACION,
    RE_OTRO_MEDIO,
    centimos,
)
from app.servicios.mundo_cerrado import montos_del_sistema, revisar_mundo_cerrado
from app.servicios.problema import NOTA, Problema
from app.servicios.turno import RE_BUSQUEDA, RE_EDAD, Acumulado

__all__ = ["NOTA", "Problema", "montos_sin_respaldo", "revisar_respuesta", "titulos_ajenos"]
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
# Solo afirmaciones explícitas: "reservado con éxito", "Estado: reservado", "ya está reservado",
# "lo reservé". "Una vez reservado, podrás pagar" explica el paso y no cuenta.
RE_RESERVADO = re.compile(
    r"\breservad[oa]s?\s+con\s+[eé]xito|\bestado\W{0,6}reservad|"
    r"\b(?:est[aá]|qued[oó]|fue|ha\s+sido)\s+reservad|\breserv[eé]\b|"
    r"\breserva\s+(?:confirmada|realizada|hecha|exitosa|lista)\b",
    re.IGNORECASE,
)


# "Al reservar, el stock queda reservado", "¿deseas reservarlo?": explican, no afirman
RE_CONDICIONAL = re.compile(r"\?|\b(?:al|si|cuando|para|puedes?|deseas?|quieres?)\s+reserv", re.I)
# "aún no está pagado", "pendiente de pago", "falta pagar"
RE_NO_PAGADO = re.compile(
    r"\b(?:no|a[uú]n no|todav[ií]a no)\s+(?:est[aá]\s+|ha sido\s+|fue\s+)?pagad|"
    r"\bpendiente de pago\b|\bfalta pagar\b|\bsin pagar\b",
    re.IGNORECASE,
)


def _oraciones(texto: str) -> list[str]:
    return [o for linea in texto.splitlines() for o in RE_ORACION.split(linea)]


def afirma_reserva(texto: str) -> bool:
    """Alguna oración dice que el pedido quedó reservado ("todavía no está reservado" no)."""
    return any(
        RE_RESERVADO.search(o) and not RE_NEGACION.search(o) and not RE_CONDICIONAL.search(o)
        for o in _oraciones(texto)
    )


def afirma_pago(texto: str) -> bool:
    """Alguna oración dice que se pagó ("aún no está pagado" no lo dice)."""
    return any(RE_PAGADO.search(o) and not RE_NO_PAGADO.search(o) for o in _oraciones(texto))


# Plazos y envíos que solo dicen las políticas: "24 horas hábiles", "1 a 3 días útiles",
# "guía de envío", "comprobante … a tu correo".
RE_PLAZO = re.compile(
    r"\b\d+\s*(?:a\s*\d+\s*)?(?:d[ií]as?|horas?)\s+(?:h[aá]biles|[uú]tiles|laborables|calendario)\b"
    r"|\bgu[ií]a de (?:env[ií]o|seguimiento)\b|\bn[uú]mero de seguimiento\b"
    r"|\bcomprobante\b[^.\n]{0,60}\bcorreo\b",
    re.IGNORECASE,
)
RE_ORACION = re.compile(r"(?<=[.!?])\s+")
# "ya quité El Silmarillion", "lo agregué", "carrito actualizado"
RE_CARRITO_CAMBIADO = re.compile(
    r"\b(?:quit[eé]|quitamos|agregu[eé]|agregamos|añad[ií]|añadimos|actualic[eé]|"
    r"actualizamos|carrito actualizado)\b",
    re.IGNORECASE,
)
# "algo de terror…", "algún libro…": pedidos que RE_BUSQUEDA no cubre
RE_ALGO = re.compile(r"\b(?:algo|alg[uú]n)\b", re.IGNORECASE)
# "quiero el libro Drácula", "me llevo el primero": pedir un libro concreto (no buscar uno).
# "Quiero ver la ficha de…" (la tarjeta del libro) pide información, no comprar: en la demo
# la regla lo tomó por compra y obligó al modelo a agregarlo al carrito.
RE_COMPRA = re.compile(
    r"\b(?:quiero|dame)\b(?!\s+(?:ver|saber|conocer|leer|m[aá]s|la\s+ficha|informaci|detalles))"
    r"|\b(?:me llevo|me lo llevo|compro|comprar|agrega|añade)\b",
    re.IGNORECASE,
)
PEDIDOS = {"actualizar_carrito", "reservar_pedido"}
# el libro es para un menor: preguntar la edad antes de recomendar es lo correcto
RE_MENOR = re.compile(
    r"\b(?:hij[oa]s?|niñ[oa]s?|sobrin[oa]s?|niet[oa]s?|alumn[oa]s?|adolescentes?|beb[eé]s?)\b",
    re.IGNORECASE,
)
RE_LECTURA = re.compile(r"\b(?:libros?|novelas?|cuentos?|leer|lectura|c[oó]mics?)\b", re.IGNORECASE)
# Lo que solo se sabe consultando el catálogo: stock, precio, valoración, disponibilidad.
RE_DATOS_CATALOGO = re.compile(
    r"\bstock\b|\bvaloraci[oó]n|\bficha\b|\bISBN\b|S/\s?\d|\bdisponibles?\b|cat[aá]logo|"
    r"\btenemos\b|\bcontamos con\b|\bc[oó]mpral[oa]s?\b|\bcomprarl[oa]s?\b|\breservarl[oa]s?\b",
    re.IGNORECASE,
)
# Lo que sigue a un título cuando el modelo lo presenta como libro: ", de J. K. Rowling".
RE_AUTOR = re.compile(r"\s*,?\s*(?:de|por)\s+[A-ZÁÉÍÓÚÑ]")
# El mensaje del cliente habla de libros ("dime por qué El alquimista tiene buena valoración").
RE_SOBRE_LIBROS = re.compile(
    r"\b(?:libros?|novelas?|cuentos?|t[ií]tulos?|autora?|sagas?|leer|le[ií]|valoraci[oó]n|"
    r"rese[ñn]as?|comprar|adivina)\b",
    re.IGNORECASE,
)
# Medios de pago y zonas que el modelo pone en negrita y nunca son libros.
NO_LIBROS = frozenset(
    {
        "tarjeta", "tarjetas", "efectivo", "transferencia", "transferencia bancaria", "yape",
        "plin", "contra entrega", "efectivo contra entrega", "lima", "provincias", "pagar",
    }
)  # fmt: skip
ETIQUETAS = frozenset(
    {
        "precio", "stock", "stock disponible", "formato", "valoracion", "isbn", "subtotal",
        "envio", "total", "sinopsis", "autor", "editorial", "paginas", "edad", "respuesta",
        "pedido", "carrito", "pago", "envio a provincias", "envio a lima metropolitana",
    }
)  # fmt: skip


def _id_real(ident: str, acumulado: Acumulado, bd: BaseDatos, usuario_id: str) -> bool:
    if ident in acumulado.pedidos or ident in acumulado.pagos:
        return True
    pedidos = bd.pedidos_de(usuario_id)
    if ident.startswith("PED-"):
        return any(p["pedido_id"] == ident for p in pedidos)
    return any(pago["id"] == ident for p in pedidos for pago in bd.pagos_de(p["pedido_id"]))


def montos_sin_respaldo(texto: str, bd: BaseDatos, usuario_id: str, mensaje: str) -> list[str]:
    """Montos en soles que no son precio, tarifa, monto de un pedido ni algo que dijo el cliente."""
    escritos = RE_MONTO.findall(texto)
    if not escritos:
        return []
    conocidos = {a_centimos(lb["precio"]) for lb in bd.libros_activos()}
    conocidos |= montos_del_sistema(bd, usuario_id, mensaje)
    return [m for m in dict.fromkeys(escritos) if centimos(m) not in conocidos]


def titulos_ajenos(texto: str, bd: BaseDatos, mensaje: str = "") -> list[str]:
    """Libros que la respuesta nombra y que no están en el catálogo.

    "**Harry Potter y la piedra filosofal**, de J. K. Rowling" no está en el catálogo: si
    el modelo lo ofrece con su stock y su valoración, los inventó. Solo cuenta como libro
    lo destacado que viene con su autor ("*Título*, de Autor") o que nombró el cliente: en
    la demo, "**Tarjeta**", "**Efectivo**" y "**Transferencia bancaria**" se tomaron por
    libros y la corrección hizo que el bot negara tener un libro que sí tenía.
    """
    conocidos = {bd.tienda.nombre, *(z.nombre for z in bd.tienda.envio.values())}
    for libro in bd.libros_activos():
        conocidos |= {libro["titulo"], libro["autor"]}
    normalizados = [f" {normalizar(c)} " for c in conocidos]
    # lo que nombra el cliente cuenta como libro solo si su mensaje habla de libros: en la demo,
    # "con transferencia bancaria" llevó al bot a decir "no tenemos el libro Transferencia…"
    pedido = f" {normalizar(mensaje)} " if RE_SOBRE_LIBROS.search(mensaje) else ""
    ajenos = []
    for marca in RE_DESTACADO.finditer(texto):
        frase = next(g for g in marca.groups() if g).strip()
        # "**S/ 73.30**" o "**PED-65CC85F3**" no son títulos: sin montos ni ids, tiene que
        # quedar al menos una palabra de cuatro letras ("**Total: S/ 73.30**" -> "Total").
        sin_cifras = RE_ID.sub("", RE_MONTO_COMPLETO.sub("", frase)).strip(" :·-")
        limpia = normalizar(sin_cifras)
        etiqueta = frase.endswith(":") or texto[marca.end() : marca.end() + 1] == ":"
        palabra = re.search(r"[^\W\d_]{4,}", sin_cifras)
        if not palabra or not frase[0].isupper() or etiqueta or limpia in ETIQUETAS | NO_LIBROS:
            continue
        con_autor = RE_AUTOR.match(texto, marca.end())
        if not (con_autor or f" {limpia} " in pedido):
            continue  # destacado, pero nada dice que sea un libro
        if not any(f" {limpia} " in c or c in f" {limpia} " for c in normalizados):
            ajenos.append(frase)
    return list(dict.fromkeys(ajenos))


def revisar_respuesta(
    texto: str,
    acumulado: Acumulado,
    bd: BaseDatos,
    usuario_id: str,
    mensaje: str = "",
    vistos: list[dict[str, Any]] | None = None,
) -> Problema | None:
    """El primer problema encontrado, o None si la respuesta se puede entregar.

    `vistos` son los mensajes que leyó el modelo en el turno: un nombre que no está en el
    catálogo ni en ellos salió de su memoria.
    """
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
    # "me das un libro de comedia o política" respondido sin herramientas: los títulos salen
    # de la memoria del modelo, no del catálogo. Preguntar la edad de un menor sí vale, y
    # "¿qué películas me recomiendas?" no es un pedido de libros.
    otro_medio = RE_OTRO_MEDIO.search(mensaje) and not RE_LECTURA.search(mensaje)
    busca = RE_BUSQUEDA.search(mensaje) and not otro_medio
    if busca and not acumulado.usadas and not RE_MENOR.search(mensaje):
        return Problema(
            motivo="recomienda sin consultar el catálogo",
            instruccion=NOTA + "No consultaste el catálogo: llama ahora a recomendar_libros (o a "
            "buscar_libros si nombró un título o autor) y recomienda solo libros que devuelva; "
            "si dio varias opciones, una búsqueda por cada una.",
            respaldo="Para recomendarte libros que sí tenemos necesito revisar el catálogo. "
            "¿Me cuentas de nuevo qué te gustaría leer?",
        )
    # "Es Harry Potter… ¿te muestro su ficha, stock y valoración?" sin haber consultado nada:
    # el libro no está en el catálogo y sus datos serían inventados.
    sin_consultar = not acumulado.usadas and not otro_medio and RE_DATOS_CATALOGO.search(texto)
    if sin_consultar and (ajenos := titulos_ajenos(texto, bd, mensaje)):
        return Problema(
            motivo=f"nombra libros que no están en el catálogo: {', '.join(ajenos)}",
            instruccion=NOTA + f"Nombraste {', '.join(ajenos)} como si estuviera en el "
            "catálogo, sin consultarlo, y no está. Llama a buscar_libros con ese título, dile "
            "al cliente con claridad que no lo tenemos y ofrece solo libros que devuelva una "
            "herramienta. No des stock, precio ni valoración de libros que no consultaste.",
            respaldo=f"No tenemos {ajenos[0]} en nuestro catálogo. Si me cuentas qué te "
            "gustó de él, te recomiendo libros parecidos que sí tenemos.",
        )
    # Lo que existe: catálogo cerrado, agotados, datos de cada libro y alcance (mundo_cerrado)
    if problema := revisar_mundo_cerrado(texto, acumulado, bd, usuario_id, mensaje, vistos):
        return problema
    # "quiero el libro Drácula": encontró ese único libro, pero pregunta si lo agrega en vez
    # de agregarlo. Con una recomendación o varios resultados, preguntar sí es razonable.
    nombres = {u["nombre"] for u in acumulado.usadas}
    # los relacionados que agrega la búsqueda no cuentan: el cliente pidió un libro
    encontrados = [lb for lb in acumulado.libros if not lb.get("relacionado")]
    concreto = len(encontrados) == 1 and "recomendar_libros" not in nombres
    compra = RE_COMPRA.search(mensaje) and not RE_BUSQUEDA.search(mensaje)
    if compra and concreto and not nombres & PEDIDOS and "?" in texto:
        return Problema(
            motivo="pide confirmación en lugar de agregar al carrito",
            instruccion=NOTA + "El cliente ya pidió el libro: llama ahora a actualizar_carrito "
            "con su ISBN y la cantidad (1 si no la dijo), con la zona si la dio, y muestra el "
            "resumen que devuelve.",
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
    # El bot no cobra: paga el cliente con el botón de la tarjeta del pedido.
    # El pago se hace con el botón, fuera del chat: el modelo no lo ve en el historial y en la
    # simulación dijo "aún no está pagado" de un pedido que ya lo estaba.
    pagados = estados or {p.get("estado") for p in bd.pedidos_de(usuario_id)}
    if RE_NO_PAGADO.search(texto) and "pagado" in pagados and "consultar_pedido" not in usadas:
        return Problema(
            motivo="dice que no está pagado un pedido que ya se pagó",
            instruccion=NOTA + "El cliente pagó con el botón «Pagar»: eso no aparece en la "
            "conversación. Llama a consultar_pedido y responde con el estado que devuelva.",
            respaldo="Revisé tu pedido: ya figura como pagado. ¿Te ayudo con algo más?",
        )
    if afirma_pago(texto) and "pagado" not in pagados:
        return Problema(
            motivo="afirma un pago que no se registró",
            instruccion=NOTA
            + "El pedido no está pagado y tú no puedes cobrar: el cliente paga eligiendo el "
            "método y pulsando «Pagar» en la tarjeta del pedido. Díselo, sin decir que ya pagó.",
            respaldo="Todavía no está pagado. Para pagar, elige el método y pulsa «Pagar» en la "
            "tarjeta del pedido.",
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
    # "¡Reservado con éxito! Pedido PED-…" sin haber llamado a reservar_pedido: en la simulación
    # de una compra completa el pedido seguía en el carrito. Sin ids en el texto, vale el estado
    # de los pedidos del cliente.
    conocidos = estados or {p.get("estado") for p in bd.pedidos_de(usuario_id)}
    if (
        afirma_reserva(texto)
        and "reservar_pedido" not in usadas
        and not conocidos & {"reservado", "pagado"}
    ):
        return Problema(
            motivo="afirma una reserva que no se registró",
            instruccion=NOTA
            + "El pedido no está reservado: no llamaste a reservar_pedido. Si el cliente pidió "
            "reservar, llámala ahora con el id exacto del pedido y muestra lo que devuelve.",
            respaldo="Todavía no reservé el pedido. ¿Quieres que lo reserve ahora?",
        )
    if RE_CARRITO_CAMBIADO.search(texto) and "actualizar_carrito" not in usadas:
        return Problema(
            motivo="afirma un cambio en el carrito que no hizo",
            instruccion=NOTA
            + "El carrito no cambió: no llamaste a actualizar_carrito. Si el cliente pidió "
            "agregar o quitar un libro, llámala ahora con la cantidad final de cada uno.",
            respaldo="Todavía no cambié el carrito. ¿Me confirmas qué libro y cuántos ejemplares?",
        )
    if sueltos := montos_sin_respaldo(texto, bd, usuario_id, mensaje):
        carrito = acumulado.carrito
        return Problema(
            motivo=f"monto que no calculó el sistema: S/ {', S/ '.join(sueltos)}",
            instruccion=NOTA
            + f"Escribiste montos que no calculó el sistema (S/ {', S/ '.join(sueltos)}). No "
            "sumes ni calcules el envío: llama a actualizar_carrito (o a consultar_pedido) y "
            "copia los montos del resumen que devuelve.",
            respaldo=carrito["resumen"]
            if carrito and "resumen" in carrito
            else "El total lo calcula el sistema con el carrito. ¿Qué libros y cuántos "
            "ejemplares quieres, y el envío es a Lima o a provincias?",
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
    # "te enviaremos el comprobante y la guía en 24 horas hábiles; llega en 2 días útiles"
    if RE_PLAZO.search(texto) and "buscar_politica" not in usadas:
        sin_plazos = " ".join(o for o in RE_ORACION.split(texto) if not RE_PLAZO.search(o))
        return Problema(
            motivo="promete plazos o comprobantes sin consultar las políticas",
            instruccion=NOTA + "Prometiste plazos, comprobantes o seguimiento que no "
            "consultaste. Quítalos, o llama a buscar_politica y usa solo lo que devuelva.",
            respaldo=sin_plazos.strip()
            or "Los plazos de entrega están en nuestras políticas. ¿Quieres que te los cuente?",
        )
    return None
