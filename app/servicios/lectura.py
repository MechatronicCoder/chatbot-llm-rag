"""Lectura de la respuesta del modelo: montos, nombres propios y cláusulas.

El catálogo y la tienda forman un vocabulario cerrado: todo libro, autor, género o
zona que el bot puede nombrar está en él. Un nombre que no está ("Biblia Reina
Valera", "Dante Alighieri", "FIL Lima") no salió de la librería sino de la memoria
del modelo. Para saber qué afirma la respuesta se lee por cláusulas: "No tengo *La
Divina Comedia*, pero tenemos *Hamlet*" niega lo primero y afirma lo segundo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.catalogo.cotizacion import a_centimos
from app.core.tarjetas import MARCAS
from app.core.texto import normalizar
from app.persistencia.db import BaseDatos

# "S/ 59.90", "S/1,234.50", "S/ 59,90", "S/. 59.90"
RE_MONTO = re.compile(r"S/\.?\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)")
RE_MONTO_COMPLETO = re.compile(r"S/\.?\s?[\d.,]+")
RE_NUMERO = re.compile(r"\d+(?:[.,]\d{1,2})?")
RE_ID = re.compile(r"\b(?:PED|PAG)-[0-9A-Z]{4,16}\b", re.IGNORECASE)
# Títulos destacados en la respuesta: **Título**, *Título*, «Título» o “Título”.
RE_DESTACADO = re.compile(
    r"\*\*([^*\n]{2,90})\*\*|(?<![*\w])\*([^*\n]{2,90})\*(?![*\w])|«([^»\n]{2,90})»|“([^”\n]{2,90})”"
)
# Pedir películas, series o música no es pedir libros, aunque diga "recomiendas".
RE_OTRO_MEDIO = re.compile(
    r"\b(?:pel[ií]culas?|series?|m[uú]sica|canci[oó]n(?:es)?|videojuegos?|podcasts?|anime)\b",
    re.IGNORECASE,
)
# "tenemos", "contamos con", "está disponible", "en stock": la respuesta dice que algo hay
RE_AFIRMA = re.compile(
    r"\b(?:tenemos|tengo|contamos con|manejamos|vendemos|ofrecemos|disponibles?|en stock|"
    r"hay (?:stock|ejemplares|unidades)|a la venta)\b",
    re.IGNORECASE,
)
RE_NEGACION = re.compile(
    r"(?<!\bsi )\b(?:no|ni|tampoco|nunca|jam[aá]s|ning[uú]n[oa]?|agotad[oa]s?|se agot[oó]|"
    r"se acab[oó])\b|\b(?:sin|fuera de) (?:stock|ejemplares|unidades|existencias)\b",
    re.IGNORECASE,
)
# Fin de oración, dos puntos, raya, "¿" y conectores que cambian de afirmación a negación.
RE_CORTE = re.compile(
    r"[.!?]+(?=[\s*_)»”\"]|$)|[:;]|(?=¿)|\s[—–]\s"
    r"|,?\s*\b(?:pero|sin embargo|aunque|en cambio|no obstante|mientras que)\b,?",
    re.IGNORECASE,
)
# Una línea de lista: viñeta, número o un título destacado al inicio (con un emoji antes)
RE_ITEM = re.compile(r"\s*(?:[^\w\s*«“\"-]\s*)*(?:[-*•+]\s|\d+[.)]\s|\*|«|“|\")")
RE_PALABRA = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
RE_ROMANO = re.compile(r"[IVXLCDM]+")
RE_LETRA = re.compile(r"[^\W\d_]")
# Lo que no es un nombre: montos, ids de pedido, enlaces y correos
RE_CIFRAS = re.compile(RE_ID.pattern + r"|S/\.?\s?[\d.,]+|https?://\S+|\S+@\S+", re.IGNORECASE)
RE_ENTRE = re.compile(r"[\s*_«»“”\"']*(?:(?:de|del|la|las|los|el|y|e|of|the|and)[\s*_]+)*", re.I)

# Palabras que una respuesta correcta escribe con mayúscula sin nombrar nada inventado.
_COMUNES = """
    si no hola claro gracias perfecto listo genial excelente bien ok okay vale buenas buenos
    dias tardes noches nota importante resumen opcion opciones recomendacion recomendaciones
    sugerencia alternativa alternativas ejemplo paso pasos total subtotal envio carrito pedido
    precio stock valoracion formato sinopsis autor autora editorial paginas edad idioma genero
    categoria libro novela cuento catalogo tienda libreria pagar reservar cancelar agregar
    comprar ficha estado reservado pagado pendiente cancelado disponible agotado cliente
    peru lima soles ia pdf epub isbn dni ruc igv cvv efectivo transferencia tarjeta provincias
    tambien ademas otras otros estas estos aqui ahi como cual cuales quien quienes donde
    cuando cuanto cuanta cuantos cuantas que porque recuerda consejo dato aviso atencion
    lunes martes miercoles jueves viernes sabado domingo enero febrero marzo abril mayo junio
    julio agosto septiembre setiembre octubre noviembre diciembre navidad
    el la los las lo un una unos unas de del al y e o u en con por para sin sobre entre
    the of and an in on to for with by from at
"""
COMUNES = frozenset(_COMUNES.split())
CONECTORES = frozenset({"de", "del", "la", "las", "los", "el", "y", "e", "of", "the", "and"})


def centimos(texto: str) -> int:
    """'1,234.50' -> 123450; '59,90' -> 5990 (coma decimal)."""
    miles = re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?", texto)
    return a_centimos(texto.replace(",", "") if miles else texto.replace(",", "."))


def palabras(texto: str) -> frozenset[str]:
    return frozenset(normalizar(texto).split())


def _forma_conocida(p: str, vocab: frozenset[str]) -> bool:
    if p.isdigit() or p in vocab or f"{p}s" in vocab or f"{p}es" in vocab:
        return True
    return (p.endswith("s") and p[:-1] in vocab) or (p.endswith("es") and p[:-2] in vocab)


def conocida(palabra: str, vocab: frozenset[str]) -> bool:
    """La palabra (o su singular o plural) está en el vocabulario."""
    return all(_forma_conocida(p, vocab) for p in normalizar(palabra).split())


@dataclass(frozen=True)
class Vocabulario:
    nombres: frozenset[str]  # títulos, autores, editoriales, géneros, la tienda y sus zonas
    todo: frozenset[str]  # además, sinopsis, temas y protagonistas


def vocabulario_catalogo(bd: BaseDatos) -> Vocabulario:
    tienda = bd.tienda
    fijos = [tienda.nombre, *(z.nombre for z in tienda.envio.values()), *(m for m, _ in MARCAS)]
    libros = tuple(
        (
            " ".join(str(lb.get(c) or "") for c in ("titulo", "autor", "editorial", "genero")),
            " ".join(str(lb.get(c) or "") for c in ("categoria", "formato", "idioma")),
            " ".join([str(lb.get("protagonista") or ""), *lb.get("temas", [])]),
            str(lb.get("sinopsis") or ""),
        )
        for lb in bd.libros_activos()
    )
    return _vocabulario(" ".join(fijos), libros)


@lru_cache(maxsize=8)
def _vocabulario(fijos: str, libros: tuple[tuple[str, str, str, str], ...]) -> Vocabulario:
    nombres = COMUNES | palabras(fijos) | palabras(" ".join(a + " " + b for a, b, _, _ in libros))
    return Vocabulario(nombres, nombres | palabras(" ".join(c + " " + d for _, _, c, d in libros)))


@dataclass(frozen=True)
class Clausula:
    inicio: int
    fin: int
    texto: str
    pregunta: bool
    negada: bool
    afirma: bool  # dice que algo está disponible
    heredada: bool  # es un ítem de la lista que sigue a "…tenemos estas ediciones:"
    primera: bool  # la primera cláusula de su línea (en un ítem, donde va el título)


class Lectura:
    """La respuesta cortada en cláusulas, con sus nombres propios y sus destacados."""

    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.plano = RE_CIFRAS.sub(lambda m: " " * len(m.group(0)), texto)
        self.destacados = [
            (m.start(g), m.end(g))
            for m in RE_DESTACADO.finditer(texto)
            for g in range(1, 5)
            if m.group(g)
        ]
        self.clausulas = list(self._clausulas())

    def _clausulas(self) -> list[Clausula]:
        resultado: list[Clausula] = []
        heredada, base = False, 0
        for linea in self.texto.split("\n"):
            inicio_linea, base = base, base + len(linea) + 1
            if not linea.strip():
                continue
            heredada = heredada and bool(RE_ITEM.match(linea))
            previo, cortes = 0, [*RE_CORTE.finditer(linea), None]
            for corte in cortes:
                fin = corte.start() if corte else len(linea)
                trozo = linea[previo:fin]
                if trozo.strip():
                    negada = bool(RE_NEGACION.search(trozo))
                    pregunta = "¿" in trozo or bool(corte and "?" in corte.group(0))
                    afirma = bool(RE_AFIRMA.search(trozo))
                    primera = not resultado or resultado[-1].inicio < inicio_linea
                    resultado.append(
                        Clausula(
                            inicio_linea + previo, inicio_linea + fin, trozo, pregunta, negada,
                            afirma, heredada, primera,
                        )
                    )  # fmt: skip
                previo = max(previo, corte.end()) if corte else previo
            ultima = resultado[-1] if resultado else None
            if linea.rstrip().endswith(":") and ultima and ultima.inicio >= inicio_linea:
                heredada = ultima.afirma and not ultima.negada and not ultima.pregunta
        return resultado

    def destacado_en(self, posicion: int) -> tuple[int, int] | None:
        return next(((a, b) for a, b in self.destacados if a <= posicion < b), None)

    def _inicial(self, posicion: int, c: Clausula) -> bool:
        """Primera palabra de la cláusula: lleva mayúscula sin ser un nombre."""
        return not RE_LETRA.search(self.plano[c.inicio : posicion])

    def corridas(self, c: Clausula) -> list[list[re.Match[str]]]:
        """Palabras con mayúscula seguidas ("Biblia Reina Valera", "Dante Alighieri")."""
        grupos: list[list[re.Match[str]]] = []
        actual: list[re.Match[str]] = []
        for m in RE_PALABRA.finditer(self.plano, c.inicio, c.fin):
            palabra = m.group(0)
            if palabra[0].isupper() and len(palabra) >= 3 and not RE_ROMANO.fullmatch(palabra):
                if actual and not RE_ENTRE.fullmatch(self.plano[actual[-1].end() : m.start()]):
                    grupos.append(actual)
                    actual = []
                actual.append(m)
            elif actual and normalizar(palabra) not in CONECTORES:
                grupos.append(actual)
                actual = []
        return [*grupos, actual] if actual else grupos

    def nombres_fuera(self, c: Clausula, vocab: frozenset[str], *, destacadas: bool) -> list[str]:
        """Nombres propios de la cláusula con alguna palabra fuera del vocabulario."""
        nombres = []
        for grupo in self.corridas(c):
            # la primera palabra solo cuenta si va en un destacado con forma de título
            # ("**Hamlet**" sí; "**¿Cómo publicar…?**" o "**Importante**:" no)
            revisadas = [
                m
                for m in grupo
                if not self._inicial(m.start(), c) or (destacadas and self._en_titulo(m.start()))
            ]
            fuera = [m for m in revisadas if not conocida(m.group(0), vocab)]
            if fuera:
                tramo = self.destacado_en(fuera[0].start())
                titulo = self._titulo(*tramo) if tramo else ""
                nombres.append(titulo or self.plano[grupo[0].start() : grupo[-1].end()])
        return nombres

    def _en_titulo(self, posicion: int) -> bool:
        tramo = self.destacado_en(posicion)
        return bool(tramo and self._titulo(*tramo))

    def _titulo(self, a: int, b: int) -> str:
        """Lo destacado entre a y b si tiene forma de título: ni frase ni etiqueta."""
        frase = self.plano[a:b].strip()
        if not frase or not frase[0].isupper() or len(frase.split()) > 12:
            return ""
        if ":" in frase or self.texto[b : b + 3].lstrip("*").startswith(":"):
            return ""  # "**Total:** S/ 50" es una etiqueta
        if RE_AFIRMA.search(frase) or RE_NEGACION.search(frase):
            return ""  # "**Sí, tenemos otras ediciones**" es una frase
        return frase

    def titulos_en(self, c: Clausula) -> list[str]:
        """Lo destacado en la cláusula que tiene forma de título."""
        return [
            t for a, b in self.destacados if c.inicio <= a < c.fin and (t := self._titulo(a, b))
        ]

    def primera_corrida(self, c: Clausula) -> str:
        grupos = self.corridas(c)
        return self.plano[grupos[0][0].start() : grupos[0][-1].end()] if grupos else ""
