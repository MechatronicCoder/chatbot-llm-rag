"""Verificación de la respuesta: ids de pedido inventados y políticas citadas sin consultar."""

from __future__ import annotations

from app.persistencia.db import BaseDatos
from app.servicios.chat import ServicioChat
from app.servicios.turno import Acumulado
from app.servicios.verificacion import revisar_respuesta
from tests.datos_prueba import ISBN_MAGO
from tests.dobles import ModeloFalso, llamada, respuesta


def eventos(servicio: ServicioChat, turno_id: str) -> list[str]:
    return [e["evento"] for e in servicio.registro.leer(limite=80, turno=turno_id)]


# -- la función ------------------------------------------------------------------
def test_un_id_inventado_o_ajeno_es_un_problema(bd: BaseDatos) -> None:
    pedido = bd.reservar_pedido("ana", ISBN_MAGO, 1)["pedido_id"]
    pago = bd.registrar_pago("ana", pedido, "efectivo")["pago_id"]
    vacio = Acumulado()
    assert revisar_respuesta(f"Tu pedido {pedido} y el pago {pago}.", vacio, bd, "ana") is None
    assert revisar_respuesta(f"tu pedido {pedido.lower()}", vacio, bd, "ana") is None
    ajeno = revisar_respuesta(f"Tu pedido {pedido}.", vacio, bd, "otro-cliente")
    assert ajeno is not None and pedido in ajeno.motivo
    inventado = revisar_respuesta("Reservé el pedido PED-9F3G7H2K.", vacio, bd, "ana")
    assert inventado is not None and "PED-9F3G7H2K" in inventado.instruccion
    assert revisar_respuesta("Pago PAG-00000000 listo.", vacio, bd, "ana") is not None
    del_turno = Acumulado(pedidos=["PED-12345678"], pagos=["PAG-87654321"])
    assert revisar_respuesta("PED-12345678 y PAG-87654321", del_turno, bd, "ana") is None


def test_citar_una_politica_exige_haberla_consultado(bd: BaseDatos) -> None:
    cita = "Según nuestra política (página 2, sección «Libros con defectos») se cambia."
    problema = revisar_respuesta(cita, Acumulado(), bd, "ana")
    assert problema is not None and "buscar_politica" in problema.instruccion
    consultada = Acumulado(usadas=[{"nombre": "buscar_politica"}])
    assert revisar_respuesta(cita, consultada, bd, "ana") is None
    assert (
        revisar_respuesta("Tiene 152 páginas y es de tapa blanda.", Acumulado(), bd, "ana") is None
    )


def test_un_pago_o_una_cancelacion_deben_estar_en_la_base(bd: BaseDatos) -> None:
    pedido = bd.reservar_pedido("ana", ISBN_MAGO, 1)["pedido_id"]
    vacio = Acumulado()
    pagado = f"Tu pedido {pedido} ya está pagado."
    problema = revisar_respuesta(pagado, vacio, bd, "ana")
    assert problema is not None and "«Pagar»" in problema.instruccion
    assert "«Pagar»" in problema.respaldo
    assert revisar_respuesta("¡Pago confirmado! Gracias.", vacio, bd, "ana") is not None
    cancelado = f"Listo, el pedido {pedido} fue cancelado."
    problema = revisar_respuesta(cancelado, vacio, bd, "ana")
    assert problema is not None and "cancelar_pedido" in problema.instruccion
    bd.cancelar_pedido(pedido)
    assert revisar_respuesta(cancelado, vacio, bd, "ana") is None
    otro = bd.reservar_pedido("ana", ISBN_MAGO, 1)["pedido_id"]
    bd.registrar_pago("ana", otro, "efectivo")
    assert revisar_respuesta(f"Tu pedido {otro} está pagado.", vacio, bd, "ana") is None


# -- el turno --------------------------------------------------------------------
def test_el_modelo_corrige_una_reserva_inventada(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta("¡Listo! Reservé tu libro. Pedido PED-9F3G7H2K."),
        respuesta(
            tool_calls=[
                llamada(
                    "actualizar_carrito", cambios=[{"isbn": ISBN_MAGO, "cantidad": 1}], zona="lima"
                ),
                llamada("reservar_pedido"),
            ]
        ),
        respuesta("Reservado."),
    ]
    r = servicio_con_modelo.responder("quiero 1 de La escuela de magos", "ana", "s-v1")
    assert r.pedidos and "PED-9F3G7H2K" not in r.respuesta
    correccion = modelo.llamadas[1]["mensajes"][-1]
    assert correccion["role"] == "user" and "PED-9F3G7H2K" in correccion["content"]
    assert "salida.no_verificada" in eventos(servicio_con_modelo, r.turno_id)


def test_si_vuelve_a_inventar_el_cliente_recibe_el_respaldo(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [respuesta("Pedido PED-AAAA1111."), respuesta("Pedido PED-BBBB2222.")]
    r = servicio_con_modelo.responder("quiero 1 de La escuela de magos", "ana", "s-v2")
    assert "PED-" not in r.respuesta and "cuántos ejemplares" in r.respuesta
    assert not r.pedidos


def test_al_agotar_las_vueltas_tambien_se_verifica(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    buscar = respuesta(tool_calls=[llamada("buscar_libros", consulta="magos")])
    modelo.respuestas = [buscar, buscar, buscar, respuesta("Según nuestra política, página 3.")]
    r = servicio_con_modelo.responder("¿tienen libros de magos?", "ana", "s-v3")
    assert "revisar nuestras políticas" in r.respuesta


def test_decir_pagado_sin_pago_lleva_al_boton(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    """Demo: "con transferencia bancaria" y el bot respondió "¡pagado con éxito!"."""
    pedido = servicio_con_modelo.bd.reservar_pedido("ana", ISBN_MAGO, 1)["pedido_id"]
    modelo.respuestas = [
        respuesta(f"¡Tu pedido {pedido} ha sido pagado con éxito por transferencia!"),
        respuesta(f"Para pagar {pedido}, elige transferencia y pulsa «Pagar»."),
    ]
    r = servicio_con_modelo.responder("con transferencia bancaria", "ana", "s-v4")
    assert "«Pagar»" in modelo.llamadas[1]["mensajes"][-1]["content"]
    assert "pagado con éxito" not in r.respuesta and not r.pagos
    assert servicio_con_modelo.bd.consultar_pedido(pedido)["estado"] == "reservado"  # type: ignore[index]


def test_preguntar_en_vez_de_recomendar_solo_cuenta_si_ya_dio_la_edad(bd: BaseDatos) -> None:
    pregunta = "¿Qué tipo de terror le gusta?"
    con_edad = "algo de terror para mi hijo de 10 años"
    problema = revisar_respuesta(pregunta, Acumulado(), bd, "ana", con_edad)
    assert problema is not None and "recomendar_libros" in problema.instruccion
    assert problema.respaldo == pregunta
    sin_edad = "quiero un libro para mi hija"
    assert revisar_respuesta("¿Qué edad tiene?", Acumulado(), bd, "ana", sin_edad) is None
    recomendo = Acumulado(usadas=[{"nombre": "recomendar_libros", "ok": True}])
    assert revisar_respuesta(pregunta, recomendo, bd, "ana", con_edad) is None


def test_el_modelo_recomienda_despues_de_la_correccion(
    servicio_con_modelo: ServicioChat, modelo: ModeloFalso
) -> None:
    modelo.respuestas = [
        respuesta("¿Qué tipo de magia le gusta a tu hijo?"),
        respuesta(tool_calls=[llamada("recomendar_libros", necesidad="magia", edad=10)]),
        respuesta("Te recomiendo La escuela de magos."),
    ]
    r = servicio_con_modelo.responder("busco algo de magia para mi hijo de 10 años", "ana", "s-v5")
    assert r.ruta == "recomendacion" and r.libros
