"""Mensajes escritos por partes: un solo turno, en orden, y sin títulos de memoria."""

from __future__ import annotations

import threading
import time
from typing import Any

from app.llm.cliente import RespuestaLLM
from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.contexto import CandadosPorSesion
from app.servicios.turno import RE_BUSQUEDA, Acumulado
from app.servicios.verificacion import revisar_respuesta
from tests.dobles import ModeloFalso, llamada, respuesta

PARTIDO = "hola\nme das un libro\npuede ser de\ncomedia? o política?"


class ModeloLento(ModeloFalso):
    """Cuenta cuántas llamadas hay a la vez."""

    def __init__(self) -> None:
        super().__init__()
        self.activas = 0
        self.maximo = 0
        self._candado = threading.Lock()

    def chat(self, *args: Any, **kwargs: Any) -> RespuestaLLM:
        with self._candado:
            self.activas += 1
            self.maximo = max(self.maximo, self.activas)
        time.sleep(0.05)
        with self._candado:
            self.activas -= 1
        return respuesta("Listo.")


def test_un_mensaje_partido_se_reconoce_como_busqueda() -> None:
    assert RE_BUSQUEDA.search(PARTIDO)
    assert RE_BUSQUEDA.search("¿tienen una novela corta?")
    assert not RE_BUSQUEDA.search("quiero devolver un libro")  # eso es de la FAQ


def test_dos_mensajes_de_la_misma_sesion_no_se_atienden_a_la_vez(
    servicio_con_modelo: ServicioChat,
) -> None:
    lento = ModeloLento()
    servicio_con_modelo.llm = lento
    hilos = [
        threading.Thread(target=servicio_con_modelo.responder, args=(m, "ana", "s-orden"))
        for m in ("quiero comprar algo", "quiero comprar otra cosa")
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join()
    assert lento.maximo == 1
    assert len(servicio_con_modelo.bd.mensajes_de("s-orden", limite=10)) == 4


def test_los_candados_son_fijos_y_estables() -> None:
    candados = CandadosPorSesion(4)
    assert candados.de("s-1") is candados.de("s-1")
    assert len({id(candados.de(f"s-{i}")) for i in range(50)}) <= 4


def test_recomendar_de_memoria_se_corrige_con_el_catalogo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta("Te recomiendo «El poder en la sombra», un ensayo político."),
        respuesta(tool_calls=[llamada("recomendar_libros", necesidad="comedia o política")]),
        respuesta("Del catálogo te recomiendo estos."),
    ]
    r = servicio_con_modelo.responder(PARTIDO, "ana", "s-partido")
    correccion = modelo.llamadas[1]["mensajes"][-1]["content"]
    assert "recomendar_libros" in correccion and "una búsqueda por cada una" in correccion
    assert r.ruta == "recomendacion" and "El poder en la sombra" not in r.respuesta


def test_si_insiste_sin_catalogo_el_cliente_no_ve_titulos_inventados(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [respuesta("Lee «Libro X»."), respuesta("Lee «Libro Y».")]
    r = servicio_con_modelo.responder(PARTIDO, "ana", "s-partido-2")
    assert "Libro" not in r.respuesta and "revisar el catálogo" in r.respuesta


def test_preguntar_la_edad_de_un_menor_sigue_permitido(bd: BaseDatos) -> None:
    pregunta = "¡Claro! ¿Qué edad tiene tu sobrino?"
    for mensaje in ("busco un libro para mi sobrino", "quiero un libro para mi nieta"):
        assert revisar_respuesta(pregunta, Acumulado(), bd, "ana", mensaje) is None
