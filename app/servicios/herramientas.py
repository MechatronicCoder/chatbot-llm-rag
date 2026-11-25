"""Ejecución de las herramientas que el modelo puede llamar (function calling).

Los esquemas están en `esquemas_herramientas.py`. Los ejecutores nunca lanzan
excepciones: devuelven `{"error": ...}` para que el modelo explique el problema
al cliente en lugar de romper la conversación.

El `usuario_id` no lo elige el modelo: viene de la sesión. Así un cliente no
puede reservar, cancelar ni consultar pedidos de otro cambiando un argumento. El modelo
no cobra: el cliente paga con el botón de la tarjeta del pedido.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.catalogo.cotizacion import resumen
from app.core.isbn import isbns_en
from app.persistencia.catalogo import en_presupuesto
from app.persistencia.db import BaseDatos, ErrorPedido
from app.servicios.esquemas_herramientas import ACCIONES, ESQUEMAS, IDEMPOTENTES
from app.servicios.rag import BuscadorDocumentos
from app.servicios.recomendador import Recomendador, resumen_libro

__all__ = ["ACCIONES", "ESQUEMAS", "IDEMPOTENTES", "Herramientas"]

Resultado = dict[str, Any]


MINIMO_RESULTADOS = 3
LIMITE_HERRAMIENTA = 10  # el modelo ve a lo más 10 libros por búsqueda, aunque pida más


def _edad(valor: Any) -> int | None:
    return None if valor is None or valor == "" else int(valor)


def _precio(valor: Any) -> float | None:
    return None if valor is None or valor == "" else float(valor)


class Herramientas:
    """Ejecuta herramientas por nombre, inyectando el usuario de la sesión."""

    def __init__(
        self, bd: BaseDatos, buscador: BuscadorDocumentos, recomendador: Recomendador
    ) -> None:
        self.bd = bd
        self.buscador = buscador
        self.recomendador = recomendador
        self._mapa: dict[str, Callable[..., Resultado]] = {
            "buscar_libros": self._buscar_libros,
            "recomendar_libros": self._recomendar_libros,
            "ficha_libro": self._ficha_libro,
            "actualizar_carrito": self._actualizar_carrito,
            "reservar_pedido": self._reservar_pedido,
            "consultar_pedido": self._consultar_pedido,
            "cancelar_pedido": self._cancelar_pedido,
            "buscar_politica": self._buscar_politica,
            "derivar_a_persona": self._derivar_a_persona,
        }

    @staticmethod
    def esquemas() -> list[dict[str, Any]]:
        return ESQUEMAS

    def ejecutar(self, nombre: str, argumentos: dict[str, Any], usuario_id: str) -> Resultado:
        ejecutor = self._mapa.get(nombre)
        if ejecutor is None:
            return {"error": f"Herramienta desconocida: {nombre!r}"}
        try:
            return ejecutor(usuario_id=usuario_id, **(argumentos or {}))
        except ErrorPedido as exc:
            return {"error": str(exc)}
        except (TypeError, ValueError) as exc:
            return {"error": f"Argumentos inválidos para {nombre}: {exc}"}
        except Exception as exc:  # nunca tumbar el turno por una herramienta
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- catálogo ----------------------------------------------------------------
    def _vivos(self, precio_min: Any = None, precio_max: Any = None) -> dict[str, Resultado]:
        """Libros activos con stock y precio actuales, dentro del presupuesto."""
        minimo, maximo = _precio(precio_min), _precio(precio_max)
        return {
            lb["isbn"]: lb for lb in self.bd.libros_activos() if en_presupuesto(lb, minimo, maximo)
        }

    def _buscar_libros(
        self,
        consulta: str = "",
        categoria: str | None = None,
        autor: str | None = None,
        edad: int | None = None,
        limite: int = 5,
        precio_max: float | None = None,
        precio_min: float | None = None,
        orden: str | None = None,
        **_: Any,
    ) -> Resultado:
        libros, correcciones = self.bd.buscar(
            consulta or "", categoria, autor, _edad(edad), min(limite or 5, LIMITE_HERRAMIENTA),
            _precio(precio_min), _precio(precio_max), orden,
        )  # fmt: skip
        resultado: Resultado = {
            "encontrados": len(libros),
            "libros": [resumen_libro(lb) for lb in libros],
        }
        if correcciones:
            resultado["correcciones"] = correcciones
        # Pocos resultados: se completan con parecidos para que el cliente tenga opciones.
        if len(libros) < MINIMO_RESULTADOS and (consulta or "").strip() and not isbns_en(consulta):
            relacionados = self.recomendador.recomendar(
                consulta,
                self._vivos(precio_min, precio_max),
                edad=_edad(edad),
                categoria=categoria,
                excluir=[lb["isbn"] for lb in libros],
                cantidad=MINIMO_RESULTADOS - len(libros),
            )
            if relacionados:
                resultado["relacionados"] = [{**lb, "relacionado": True} for lb in relacionados]
        return resultado

    def _recomendar_libros(
        self,
        necesidad: str = "",
        edad: int | None = None,
        categoria: str | None = None,
        excluir: Sequence[str] | None = None,
        cantidad: int = 3,
        precio_max: float | None = None,
        precio_min: float | None = None,
        **_: Any,
    ) -> Resultado:
        if not (necesidad or "").strip():
            return {"error": "Falta describir qué busca el cliente."}
        recomendaciones = self.recomendador.recomendar(
            necesidad,
            self._vivos(precio_min, precio_max),
            edad=_edad(edad),
            categoria=categoria,
            excluir=excluir or (),
            cantidad=cantidad or 3,
        )
        if not recomendaciones:
            return {
                "error": "No hay libros del catálogo con stock que se parezcan a lo pedido "
                "(ni dentro del presupuesto, si lo hay). Preguntar por otro tema, edad, autor o "
                "presupuesto; no inventar títulos."
            }
        return {"recomendaciones": recomendaciones}

    def _ficha_libro(self, isbn: str = "", **_: Any) -> Resultado:
        libro = self.bd.obtener_libro(isbn)
        if libro is None:
            return {"error": f"No hay un libro con ISBN {isbn!r} en el catálogo."}
        similares = self.bd.libros_por_isbn(libro["similares"])
        disponibles = [
            resumen_libro(similares[s], largo_sinopsis=100)
            for s in libro["similares"]
            if s in similares and similares[s]["stock"] > 0
        ]
        return {**libro, "similares_disponibles": disponibles}

    # -- carrito y pedidos -------------------------------------------------------
    def _con_resumen(self, pedido: Resultado) -> Resultado:
        """El texto con las cuentas hechas: el modelo lo muestra en vez de sumar."""
        return {**pedido, "resumen": resumen(pedido, self.bd.tienda)}

    def _actualizar_carrito(
        self,
        usuario_id: str,
        cambios: Sequence[dict[str, Any]] | None = None,
        zona: str | None = None,
        **_: Any,
    ) -> Resultado:
        return self._con_resumen(self.bd.actualizar_carrito(usuario_id, cambios or (), zona))

    def _reservar_pedido(self, usuario_id: str, **_: Any) -> Resultado:
        return self._con_resumen(self.bd.reservar_carrito(usuario_id))

    def _consultar_pedido(self, usuario_id: str, pedido_id: str = "", **_: Any) -> Resultado:
        if not (pedido_id or "").strip():
            # "¿ya está pagado mi pedido?": sin id, el último pedido del cliente de la sesión.
            # Antes el modelo le pedía el id al cliente aunque lo tenía en el historial.
            pedidos = self.bd.pedidos_de(usuario_id)
            if not pedidos:
                return {"error": "Este cliente no tiene pedidos."}
            otros = [
                {"pedido_id": p["pedido_id"], "estado": p["estado"], "total": p["total"]}
                for p in pedidos[1:5]
            ]
            return {**self._con_resumen(pedidos[0]), "otros_pedidos": otros}
        pedido = self.bd.consultar_pedido(pedido_id)
        if pedido is None or pedido["usuario_id"] != usuario_id:
            # mismo mensaje en ambos casos: no se revela si existe un pedido ajeno
            return {"error": f"No existe el pedido {pedido_id!r} para este cliente."}
        return self._con_resumen(pedido)

    def _cancelar_pedido(self, usuario_id: str, pedido_id: str = "", **_: Any) -> Resultado:
        pedido = self.bd.consultar_pedido(pedido_id)
        if pedido is None:
            return {"error": f"No existe el pedido {pedido_id!r}."}
        if pedido["usuario_id"] != usuario_id:
            return {"error": "Ese pedido pertenece a otro cliente."}
        return self._con_resumen(self.bd.cancelar_pedido(pedido_id))

    # -- atención humana -----------------------------------------------------------
    def _derivar_a_persona(self, motivo: str = "", **_: Any) -> Resultado:
        contacto = self.bd.tienda.contacto
        if contacto is None:
            return {"error": "La tienda no tiene datos de contacto configurados."}
        return {
            "derivado": True,
            "motivo": (motivo or "")[:200],
            **contacto.model_dump(),
            "mensaje": "Una persona del equipo continúa la atención por estos medios.",
        }

    # -- documentos --------------------------------------------------------------
    def _buscar_politica(self, pregunta: str = "", **_: Any) -> Resultado:
        hallazgos = self.buscador.buscar(pregunta, k=self.buscador.parametros.k_final)
        if not hallazgos:
            return {"error": "No hay documentos relacionados con esa consulta."}
        return {"hallazgos": hallazgos}
