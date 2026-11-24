"""Lectura de la respuesta y falsos positivos del mundo cerrado.

Una respuesta correcta no debe frenarse: los libros, autores y zonas del catálogo, lo que
el modelo leyó en el turno (políticas, resultados, el nombre que dio el cliente) y las
negaciones honestas pasan sin corrección.
"""

from __future__ import annotations

from app.persistencia.db import BaseDatos
from app.servicios.lectura import Lectura, conocida, palabras, vocabulario_catalogo
from app.servicios.mundo_cerrado import texto_visto
from app.servicios.problema import NOTA
from app.servicios.turno import Acumulado
from app.servicios.verificacion import revisar_respuesta


def con(*herramientas: str) -> Acumulado:
    return Acumulado(usadas=[{"nombre": h} for h in herramientas])


def pasa(texto: str, bd: BaseDatos, mensaje: str = "", **extra: object) -> bool:
    acumulado = extra.pop("acumulado", None) or Acumulado()
    vistos = extra.pop("vistos", None)
    problema = revisar_respuesta(texto, acumulado, bd, "ana", mensaje, vistos)  # type: ignore[arg-type]
    return problema is None


# -- lectura por cláusulas ----------------------------------------------------------------
def test_corta_en_oraciones_preguntas_y_conectores() -> None:
    lectura = Lectura("No tengo *X*, pero tenemos *Y*. ¿Quieres verlo? Sí: lo tenemos")
    resumen = [(c.texto.strip(" ,"), c.negada, c.afirma, c.pregunta) for c in lectura.clausulas]
    assert resumen == [
        ("No tengo *X*", True, True, False),
        ("tenemos *Y*", False, True, False),
        ("¿Quieres verlo", False, False, True),
        ("Sí", False, False, False),
        ("lo tenemos", False, True, False),
    ]


def test_la_lista_hereda_la_afirmacion_hasta_otro_parrafo() -> None:
    texto = "Tenemos estas ediciones:\n\n- **Uno**\n1. **Dos**\nOtro párrafo.\n- **Tres**"
    heredadas = [c.texto.strip() for c in Lectura(texto).clausulas if c.heredada]
    assert heredadas == ["- **Uno**", "1", "**Dos**"]
    # una lista después de una negación no afirma nada
    assert not any(c.heredada for c in Lectura("No tenemos estas:\n- **Uno**").clausulas)


def test_un_destacado_que_es_etiqueta_o_frase_no_es_un_titulo() -> None:
    lectura = Lectura("**Total:** S/ 50 · **Sí, tenemos stock** · **Precio**: 3 · **Un Libro**")
    assert [t for c in lectura.clausulas for t in lectura.titulos_en(c)] == ["Un Libro"]
    assert lectura.primera_corrida(lectura.clausulas[0]) == "Total"
    assert Lectura("sin nombres").primera_corrida(Lectura("sin nombres").clausulas[0]) == ""


def test_conocida_acepta_singular_y_plural() -> None:
    vocab = palabras("biblias reyes cuento")
    assert all(conocida(p, vocab) for p in ("Biblia", "Reyes", "Rey", "Cuentos", "1960"))
    assert not conocida("Hamlet", vocab)


def test_el_vocabulario_del_catalogo_se_reutiliza(bd: BaseDatos) -> None:
    primero, segundo = vocabulario_catalogo(bd), vocabulario_catalogo(bd)
    assert primero is segundo  # misma base, mismo vocabulario: no se recalcula en cada turno
    assert {"varita", "lima", "visa"} <= primero.nombres and "dibujos" in primero.todo


def test_lo_visto_no_incluye_el_borrador_rechazado_ni_la_correccion() -> None:
    mensajes = [
        {"role": "system", "content": "Eres el librero."},
        {"role": "user", "content": "hola, soy Carlos"},
        {"role": "assistant", "content": None, "tool_calls": []},
        {"role": "assistant", "content": "Tenemos la Biblia"},
        {"role": "user", "content": NOTA + "Dijiste que tenemos Biblia"},
    ]
    assert texto_visto(mensajes) == "Eres el librero.\nhola, soy Carlos\n"


# -- respuestas correctas que no se frenan -------------------------------------------------
def test_los_libros_y_datos_del_catalogo_pasan(bd: BaseDatos) -> None:
    texto = (
        "Sí, tenemos *La escuela de magos*, de Rosa Varita: un niño descubre que es mago. "
        "Cuesta S/ 69.90 y quedan 8. También te puede gustar **El hijo del mar**."
    )
    assert pasa(texto, bd, "tienes la escuela de magos?", acumulado=con("buscar_libros"))
    assert pasa("Sí, tenemos 8 en stock.", bd, "hay stock?", acumulado=con("ficha_libro"))


def test_lo_que_el_modelo_leyo_en_el_turno_pasa(bd: BaseDatos) -> None:
    politica = [{"role": "tool", "content": '{"texto": "Reclamos ante Indecopi en 15 días"}'}]
    texto = "Puedes presentar tu reclamo ante Indecopi, como dice la política."
    assert pasa(texto, bd, "donde reclamo?", acumulado=con("buscar_politica"), vistos=politica)
    assert not pasa(texto, bd, "donde reclamo?", acumulado=con("buscar_politica"))
    assert pasa("¡Claro, Carlos! ¿Qué libro buscas?", bd, "hola, soy Carlos")


def test_la_tienda_las_zonas_y_los_medios_de_pago_pasan(bd: BaseDatos) -> None:
    textos = [
        "¡Hola! Soy el librero virtual de Librería Letra Viva. ¿Qué libro buscas?",
        "El envío a Lima Metropolitana cuesta S/ 10.00; tenemos envío a provincias.",
        "Tienes guardada tu Visa •••• 4242: elígela al pulsar «Pagar».",
        "Contamos con pago con tarjeta, efectivo o transferencia.",
    ]
    assert all(pasa(t, bd, "info") for t in textos)


def test_una_negacion_honesta_despues_de_buscar_pasa(bd: BaseDatos) -> None:
    texto = "No tenemos *Cien años de soledad* en el catálogo."
    assert pasa(texto, bd, "tienes cien años de soledad?", acumulado=con("buscar_libros"))
    # el autor no lo dijo el cliente ni lo devolvió una herramienta: sale de la memoria
    autor = "No tenemos *Cien años de soledad*, de Gabriel García Márquez."
    assert not pasa(autor, bd, "tienes cien años de soledad?", acumulado=con("buscar_libros"))
    # una película no se busca en el catálogo
    assert pasa("No vendemos películas, solo libros.", bd, "venden películas como Titanic?")
