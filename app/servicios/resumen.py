"""Resumen encadenado de las conversaciones largas.

El modelo ve solo los últimos mensajes de la sesión (`MAX_MENSAJES`). Lo que queda
fuera de esa ventana se resume y el resumen va al prompt: así no olvida qué libro
eligió el cliente hace diez mensajes ni el presupuesto que dio al principio.

- Se resume cada vez que salen de la ventana al menos 8 mensajes nuevos (4 turnos),
  no en cada turno, y en segundo plano: el cliente no espera por el resumen.
- Cada resumen parte del anterior y suma lo nuevo, así crece poco.
- Los ISBN y los ids de pedido y de pago se copian tal cual al final, sin pasar
  por el modelo: un resumen no debe cambiar un dato exacto.
- Si el modelo falla, queda el resumen anterior y el turno sigue igual.
"""

from __future__ import annotations

import re
import threading

from app.core.isbn import RE_ISBN
from app.core.logs import RegistroEventos
from app.llm.cliente import ModeloChat, SinProveedores
from app.persistencia.db import BaseDatos
from app.servicios.contexto import RUTA_FILTRO

MINIMO_NUEVOS = 8
RE_DATO = re.compile(r"\b(?:PED|PAG)-[0-9A-Z]{4,16}\b|" + RE_ISBN.pattern, re.IGNORECASE)
DATOS = "Datos exactos:"
INSTRUCCION = (
    "Resume la conversación entre un cliente y el librero virtual de una librería, para que "
    "el librero la retome. En no más de 120 palabras: qué busca el cliente, para quién, sus "
    "gustos, presupuesto, libros que le interesaron o descartó, qué hay en su carrito o sus "
    "pedidos y qué quedó pendiente. Solo hechos de la conversación, sin inventar ni saludar."
)


def datos_exactos(*textos: str) -> list[str]:
    """ISBN e ids de pedido o pago, sin repetir y en el orden en que aparecen."""
    return list(dict.fromkeys(m.group(0).upper() for t in textos for m in RE_DATO.finditer(t)))


class Resumidor:
    """Mantiene al día el resumen de cada sesión larga."""

    def __init__(
        self,
        bd: BaseDatos,
        llm: ModeloChat,
        registro: RegistroEventos,
        ventana: int,
        *,
        en_segundo_plano: bool = True,
    ) -> None:
        self.bd = bd
        self.llm = llm
        self.registro = registro
        self.ventana = ventana
        self.en_segundo_plano = en_segundo_plano
        self._en_curso: set[str] = set()
        self._candado = threading.Lock()

    def pendientes(self, sesion_id: str) -> int:
        """Mensajes que salieron de la ventana y el resumen todavía no cubre."""
        previo = self.bd.resumen_de(sesion_id) or {"cubiertos": 0}
        return self.bd.contar_mensajes(sesion_id) - self.ventana - previo["cubiertos"]

    def programar(self, sesion_id: str) -> None:
        """Después de cada turno: resume si hace falta, sin hacer esperar al cliente."""
        # Dos consultas baratas evitan lanzar un hilo en los turnos que no lo necesitan.
        if not self.llm.configurado or self.pendientes(sesion_id) < MINIMO_NUEVOS:
            return
        with self._candado:
            if sesion_id in self._en_curso:
                return  # ya hay un resumen de esta sesión en marcha
            self._en_curso.add(sesion_id)
        if self.en_segundo_plano:
            threading.Thread(
                target=self._actualizar_y_soltar, args=(sesion_id,), daemon=True
            ).start()
        else:
            self._actualizar_y_soltar(sesion_id)

    def _actualizar_y_soltar(self, sesion_id: str) -> None:
        try:
            self.actualizar(sesion_id)
        finally:
            with self._candado:
                self._en_curso.discard(sesion_id)

    def actualizar(self, sesion_id: str) -> bool:
        """Suma al resumen los mensajes que salieron de la ventana; True si lo cambió."""
        fuera = self.bd.contar_mensajes(sesion_id) - self.ventana
        previo = self.bd.resumen_de(sesion_id) or {"texto": "", "cubiertos": 0}
        nuevos = self.bd.mensajes_entre(sesion_id, previo["cubiertos"], fuera)
        if len(nuevos) < MINIMO_NUEVOS:
            return False
        lineas = "\n".join(
            f"{'Cliente' if m['rol'] == 'user' else 'Librero'}: {m['contenido_redactado']}"
            for m in nuevos
            if m.get("ruta") != RUTA_FILTRO  # lo que frenó una guardia no se retoma
        )
        anterior = previo["texto"].split(DATOS)[0].strip()
        pedido = (f"Resumen anterior:\n{anterior}\n\n" if anterior else "") + lineas
        try:
            resp = self.llm.chat(
                [{"role": "system", "content": INSTRUCCION}, {"role": "user", "content": pedido}],
                temperatura=0.0,
                max_tokens=300,
            )
        except SinProveedores as exc:
            self.registro.aviso("resumen.fallo", sesion=sesion_id, motivo=str(exc)[:120])
            return False
        texto = resp.texto.split(DATOS)[0].strip()
        if datos := datos_exactos(previo["texto"], lineas):
            texto += f"\n{DATOS} {', '.join(datos)}"
        tokens = resp.uso.get("prompt_tokens", 0) + resp.uso.get("completion_tokens", 0)
        self.bd.guardar_resumen(sesion_id, texto, fuera, tokens)
        self.registro.info("resumen.actualizado", sesion=sesion_id, cubiertos=fuera, tokens=tokens)
        return True
