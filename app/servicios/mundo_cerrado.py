"""Mundo cerrado: el bot solo afirma lo que sabe la librería.

Las otras verificaciones miran acciones (ids, pagos, montos). Estas miran qué existe:

1. Dice que tenemos algo que no está en el catálogo. En la demo: "contamos con diversas
   ediciones de la Biblia", y "*Cien años de soledad*, *Don Quijote*, *Hamlet*" como
   clásicos "que sí tenemos" después de una búsqueda que no los devolvió.
2. Ofrece un libro agotado, o da de un libro un precio, una valoración o un stock que no
   son los suyos.
3. Dice que no tenemos un libro sin haber consultado el catálogo.
4. Responde fuera de la librería: "2 + 2 = 4", "la escribió Dante Alighieri",
   "editoriales como Hueso Húmero o La Soga", aunque el cliente lo pida.
5. Cuenta cómo trabaja la tienda ("no aceptamos autores independientes") sin consultarlo.
"""

from __future__ import annotations

import re
from typing import Any

from app.catalogo.cotizacion import a_centimos, montos_en_centimos
from app.core.texto import normalizar
from app.persistencia.db import BaseDatos
from app.servicios.lectura import (
    COMUNES,
    RE_MONTO,
    RE_NUMERO,
    RE_OTRO_MEDIO,
    Lectura,
    centimos,
    conocida,
    palabras,
    vocabulario_catalogo,
)
from app.servicios.problema import NOTA, Problema
from app.servicios.turno import Acumulado

CATALOGO = frozenset({"buscar_libros", "recomendar_libros", "ficha_libro"})
POLITICAS = frozenset({"buscar_politica", "derivar_a_persona"})
# Envíos, pagos o contacto: lo que "tenemos" ahí no es un libro.
RE_TEMA_TIENDA = re.compile(
    r"\b(?:env[ií]os?|pag(?:o|os|ar)|tarjetas?|efectivo|transferencias?|entregas?|horarios?|"
    r"atenci[oó]n|facturas?|boletas?|comprobantes?|devoluci|cambios? de|reclam|whatsapp|"
    r"tel[eé]fono|correo|local|sede|indecopi|garant[ií]a|informaci[oó]n|datos)",
    re.IGNORECASE,
)
RE_AGOTADO = re.compile(
    r"agotad|se agot|sin (?:stock|ejemplares|existencias)|fuera de stock|"
    r"no (?:hay|tenemos|quedan?) (?:stock|ejemplares)|\b0 en stock|stock:? 0\b",
    re.IGNORECASE,
)
# Una cuenta ("2 + 2 = 4") o un bloque de código: nada de eso sale de la librería.
RE_CALCULO = re.compile(r"\d+(?:[.,]\d+)?\s*(?:[+×*÷^]|\s[x/-]\s)\s*\d+(?:[.,]\d+)?\s*=|```")
# Cómo trabaja la tienda: qué acepta, con quién trabaja, qué evalúa.
RE_PRACTICA = re.compile(
    r"\b(?:aceptamos|trabajamos\s+(?:con|solo|solamente|[uú]nicamente|directamente)|evaluamos|"
    r"podemos\s+evaluar|publicamos|recibimos\s+(?:manuscritos|propuestas|obras)|"
    r"compramos\s+(?:libros|manuscritos|obras|colecciones)|contactarnos\s+directamente|"
    r"deben?\s+contactarnos|nuestr[oa]s\s+(?:proveedores|distribuidores|socios|convenios))\b",
    re.IGNORECASE,
)
RE_MEDIOS = re.compile(
    r"\b(?:tarjetas?|efectivo|transferencias?|visa|mastercard|yape|plin|pagos?)\b", re.IGNORECASE
)
RE_VALORACION = re.compile(
    r"(?:⭐|★|valoraci[oó]n(?:\s+de)?\s*:?)\s*(\d(?:[.,]\d{1,2})?)"
    r"|\b(\d(?:[.,]\d{1,2})?)\s*(?:⭐|★|estrellas)",
    re.IGNORECASE,
)
RE_STOCK = re.compile(
    r"\b(\d+)\s+(?:en stock|disponibles?)\b|\bstock(?:\s+disponible)?\s*:?\s*(\d+)\b"
    r"|\bquedan\s+(?:solo\s+)?(\d+)\b",
    re.IGNORECASE,
)
# "Los 5 libros mejor puntuados", "el más barato": un orden que solo da el catálogo ordenado.
RE_RANKING = re.compile(
    r"\b(?:l[oa]s|el|la)[\s*_]+(?:\d+[\s*_]+)?(?:(?:libros?|t[ií]tulos?)[\s*_]+)?"
    r"(?:(?:mejor|m[aá]s)\s+(?P<valoracion>puntuad|valorad|calificad)"
    r"|m[aá]s\s+(?P<precio_asc>barat|econ[oó]mic)|m[aá]s\s+(?P<precio_desc>car[oa]s?\b))",
    re.IGNORECASE,
)
RESPUESTA_ALCANCE = (
    "Solo puedo ayudarte con lo de la librería: buscar y recomendar libros de nuestro "
    "catálogo, tu carrito y tus pedidos, y nuestras políticas. ¿Qué libro buscas?"
)


def texto_visto(mensajes: list[dict[str, Any]]) -> str:
    """Lo que el modelo leyó en el turno, sin su borrador rechazado ni la corrección.

    Incluye lo que buscó: si buscó "Harry Potter" y no estaba, puede decir que no lo encontró.
    """
    partes = []
    for i, m in enumerate(mensajes):
        contenido = str(m.get("content") or "")
        siguiente = str(mensajes[i + 1].get("content") or "") if i + 1 < len(mensajes) else ""
        if contenido.startswith(NOTA) or (m["role"] == "assistant" and siguiente.startswith(NOTA)):
            continue
        partes.append(contenido)
        partes += [str(tc["function"]["arguments"]) for tc in m.get("tool_calls") or []]
    return "\n".join(partes)


def montos_del_sistema(bd: BaseDatos, usuario_id: str, mensaje: str) -> set[int]:
    """Montos de los pedidos del cliente, tarifas de envío y cifras que dijo el cliente."""
    conocidos: set[int] = set()
    for pedido in bd.pedidos_de(usuario_id):
        conocidos |= montos_en_centimos(pedido)
    for tarifa in bd.tienda.envio.values():
        conocidos |= {a_centimos(v) for v in (tarifa.costo, tarifa.gratis_desde) if v is not None}
    return conocidos | {centimos(n) for n in RE_NUMERO.findall(mensaje)}  # "menos de 50 soles"


Titulos = list[tuple[str, dict[str, Any]]]


def indice_de_titulos(bd: BaseDatos) -> Titulos:
    """Cada título normalizado y sin la saga entre paréntesis ("dioses menores")."""
    pares = [(normalizar(re.sub(r"\(.*?\)", " ", lb["titulo"])), lb) for lb in bd.libros_activos()]
    return [(f" {base} ", lb) for base, lb in pares if len(base) >= 5]


def libros_nombrados(texto: str, titulos: Titulos) -> list[dict[str, Any]]:
    """Libros del catálogo cuyo título aparece en el texto."""
    plano = f" {normalizar(texto)} "
    hallados = [(t, lb) for t, lb in titulos if t in plano]
    # "dioses" no cuenta si está dentro de "dioses menores"
    return [lb for t, lb in hallados if not any(t != o and t in o for o, _ in hallados)]


def afirmados_fuera_del_catalogo(lectura: Lectura, bd: BaseDatos) -> list[str]:
    voc = vocabulario_catalogo(bd)
    nombres: list[str] = []
    for c in lectura.clausulas:
        if c.negada or c.pregunta or not (c.afirma or c.heredada):
            continue
        if RE_TEMA_TIENDA.search(c.texto):
            continue
        titulos = lectura.titulos_en(c)
        nombres += [t for t in titulos if not conocida(t, voc.nombres)]
        if c.afirma:
            nombres += lectura.nombres_fuera(c, voc.todo, destacadas=False)
        elif c.primera and not titulos and (primera := lectura.primera_corrida(c)):
            # "- Biblia Reina Valera 1960" sí; "- También…" no (es una palabra común)
            nombres += [] if conocida(primera, voc.todo) else [primera]
    return list(dict.fromkeys(nombres))


def agotados_ofrecidos(lectura: Lectura, titulos: Titulos) -> list[str]:
    """Libros sin stock que la respuesta ofrece sin decir que están agotados."""
    texto = lectura.texto
    ofrecidos = []
    for c in lectura.clausulas:
        fin = texto.find("\n", c.fin)
        linea = texto[texto.rfind("\n", 0, c.inicio) + 1 : fin if fin >= 0 else len(texto)]
        if c.negada or c.pregunta or RE_AGOTADO.search(linea):
            continue
        ofrecidos += [lb["titulo"] for lb in libros_nombrados(c.texto, titulos) if lb["stock"] <= 0]
    return list(dict.fromkeys(ofrecidos))


def datos_errados(
    texto: str, titulos: Titulos, del_sistema: set[int], acumulado: Acumulado
) -> list[str]:
    """Precio, valoración o stock escritos junto a un libro que no son los de ese libro."""
    errados = []
    for linea in texto.splitlines():
        libros = libros_nombrados(linea, titulos)
        if len(libros) != 1:
            continue  # sin libro, o con varios: no se sabe de cuál es cada dato
        lb = libros[0]
        precio = a_centimos(lb["precio"])
        for escrito in RE_MONTO.findall(linea):
            if centimos(escrito) not in del_sistema | {precio}:
                errados.append(f"{lb['titulo']} a S/ {escrito} (cuesta S/ {lb['precio']:.2f})")
        for a, b in RE_VALORACION.findall(linea):
            valor = float((a or b).replace(",", "."))
            if abs(valor - float(lb["valoracion"] or 0)) > 0.05:
                errados.append(f"{lb['titulo']} con valoración {a or b} (tiene {lb['valoracion']})")
        vistos = {lb["stock"]} | {v["stock"] for v in acumulado.libros if v["isbn"] == lb["isbn"]}
        for grupos in RE_STOCK.findall(linea):
            cantidad = int(next(g for g in grupos if g))
            if cantidad not in vistos:
                errados.append(f"{lb['titulo']} con {cantidad} en stock (hay {lb['stock']})")
    return list(dict.fromkeys(errados))


def negados_sin_consultar(lectura: Lectura) -> list[str]:
    """ "No tenemos *La Divina Comedia*" dicho sin haber buscado en el catálogo."""
    nombres: list[str] = []
    for c in lectura.clausulas:
        if not c.negada or c.pregunta or not c.afirma:
            continue
        if RE_TEMA_TIENDA.search(c.texto) or RE_OTRO_MEDIO.search(c.texto):
            continue
        nombres += lectura.titulos_en(c) or lectura.nombres_fuera(c, COMUNES, destacadas=True)
    return list(dict.fromkeys(nombres))


def fuera_de_la_libreria(lectura: Lectura, bd: BaseDatos, visto: str) -> list[str]:
    """Nombres que no vienen ni del catálogo ni de lo que el modelo leyó, y cuentas.

    También en una negación: "no tengo *Hamlet*, *Don Quijote* ni otros clásicos" o "ninguno
    es el clásico de Dante" nombran lo que no hay y que el cliente no pidió.
    """
    vocab = vocabulario_catalogo(bd).todo | palabras(visto)
    nombres = [
        n for c in lectura.clausulas for n in lectura.nombres_fuera(c, vocab, destacadas=True)
    ]
    calculos = [m.group(0).strip() for m in RE_CALCULO.finditer(lectura.texto)]
    return list(dict.fromkeys(nombres + calculos))


def ranking_sin_ordenar(texto: str, acumulado: Acumulado) -> str | None:
    """El orden que la respuesta afirma ("los mejor puntuados") si no salió de buscar_libros."""
    if not (ranking := RE_RANKING.search(texto)):
        return None
    orden = next(nombre for nombre, valor in ranking.groupdict().items() if valor)
    for usada in acumulado.usadas:
        argumentos = usada.get("argumentos") or {}
        pedido = argumentos.get("orden") or "valoracion"
        if usada["nombre"] == "buscar_libros" and usada.get("ok", True) and pedido == orden:
            return None
    return orden


def practicas_sin_consultar(lectura: Lectura) -> bool:
    return any(
        RE_PRACTICA.search(c.texto) and not RE_MEDIOS.search(c.texto) and not c.pregunta
        for c in lectura.clausulas
    )


def revisar_mundo_cerrado(
    texto: str,
    acumulado: Acumulado,
    bd: BaseDatos,
    usuario_id: str,
    mensaje: str = "",
    vistos: list[dict[str, Any]] | None = None,
) -> Problema | None:
    lectura = Lectura(texto)
    usadas = {u["nombre"] for u in acumulado.usadas}
    if ajenos := afirmados_fuera_del_catalogo(lectura, bd):
        lista = ", ".join(ajenos)
        return Problema(
            motivo=f"afirma tener algo que no está en el catálogo: {lista}",
            instruccion=NOTA + f"Dijiste que tenemos {lista}, y eso no está en el catálogo: "
            "solo tenemos lo que devuelven las herramientas, aunque el cliente lo nombre. Llama "
            "a buscar_libros con lo que pidió; si no lo encuentra, dile con claridad que no lo "
            "tenemos y ofrece solo libros que devuelva una herramienta, con sus datos exactos.",
            respaldo=f"«{ajenos[0]}» no está en nuestro catálogo. Si me cuentas qué te "
            "interesa, te recomiendo libros que sí tenemos.",
        )
    titulos = indice_de_titulos(bd)
    if agotados := agotados_ofrecidos(lectura, titulos):
        lista = ", ".join(agotados)
        return Problema(
            motivo=f"ofrece libros agotados: {lista}",
            instruccion=NOTA + f"{lista} está agotado (stock 0). No lo ofrezcas: si el cliente "
            "lo pidió, dile que está agotado; recomienda solo libros con stock.",
            respaldo=f"{agotados[0]} está agotado por ahora. Si quieres, te recomiendo "
            "libros parecidos que sí tenemos en stock.",
        )
    del_sistema = montos_del_sistema(bd, usuario_id, mensaje)
    if errados := datos_errados(texto, titulos, del_sistema, acumulado):
        lista = "; ".join(errados)
        return Problema(
            motivo=f"datos que no son los del catálogo: {lista}",
            instruccion=NOTA + f"Estos datos no son los del catálogo: {lista}. Corrige la "
            "respuesta con el precio, la valoración y el stock exactos que devolvió la "
            "herramienta (consulta ficha_libro si no los tienes).",
            respaldo="Para darte el precio, el stock y la valoración exactos necesito revisar "
            "la ficha del libro. ¿De cuál quieres el detalle?",
        )
    if orden := ranking_sin_ordenar(texto, acumulado):
        return Problema(
            motivo=f"presenta un orden ({orden}) que no salió del catálogo ordenado",
            instruccion=NOTA + "Presentaste libros como los mejor valorados, los más baratos o "
            "los más caros sin pedirlos ordenados. Llama a buscar_libros con consulta vacía (o "
            f'el género que pidió) y orden="{orden}", y muestra exactamente los que devuelva.',
            respaldo="Para darte ese orden necesito ordenar el catálogo. ¿Lo quieres de algún "
            "género en particular?",
        )
    if not usadas & CATALOGO and (negados := negados_sin_consultar(lectura)):
        lista = ", ".join(negados)
        return Problema(
            motivo=f"niega tener un libro sin consultar el catálogo: {lista}",
            instruccion=NOTA + f"Dijiste que no tenemos {lista} sin consultar el catálogo. "
            "Llama a buscar_libros antes de responder y usa solo lo que devuelva.",
            respaldo="Déjame revisarlo en el catálogo: ¿me confirmas el título o el autor?",
        )
    visto = f"{mensaje}\n{texto_visto(vistos or [])}"
    if ajenos := fuera_de_la_libreria(lectura, bd, visto):
        lista = ", ".join(ajenos)
        return Problema(
            motivo=f"responde fuera de la librería: {lista}",
            instruccion=NOTA + f"Tu respuesta usa información que no viene de la librería "
            f"({lista}). Solo atiendes el catálogo, los pedidos y las políticas de la tienda: no "
            "respondes cálculos, cultura general ni datos de libros que no devolvió una "
            "herramienta, aunque el cliente insista. Dile en una frase que con eso no puedes "
            "ayudar; si su mensaje también pide algo de la librería, respóndelo con las "
            "herramientas.",
            respaldo=RESPUESTA_ALCANCE,
        )
    if not usadas & POLITICAS and practicas_sin_consultar(lectura):
        return Problema(
            motivo="afirma cómo trabaja la tienda sin consultar las políticas",
            instruccion=NOTA + "Dijiste cómo trabaja la tienda (qué acepta, con quién trabaja) "
            "sin consultarlo. Llama a buscar_politica; si no responde la pregunta, dile al "
            "cliente que no tienes esa información y ofrécele derivar_a_persona.",
            respaldo="No tengo esa información en nuestras políticas. Si quieres, te comunico "
            "con una persona del equipo.",
        )
    return None
