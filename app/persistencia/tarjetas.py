"""Tarjetas guardadas del cliente: solo la marca y los últimos cuatro dígitos.

El número completo entra, se valida y se descarta en la misma función: no se guarda,
no se registra en el log y no llega al modelo. Con lo guardado basta para mostrar
"Visa •••• 4242" al pagar; el cobro de la demo es simulado.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.tarjetas import etiqueta, luhn_valido, marca_de, solo_digitos
from app.persistencia.conexion import Conexion
from app.persistencia.esquema import ErrorPedido, momento_actual


def _tarjeta(fila: Any) -> dict[str, Any]:
    return {
        "id": fila["id"],
        "marca": fila["marca"],
        "ultimos4": fila["ultimos4"],
        "etiqueta": etiqueta(fila["marca"], fila["ultimos4"]),
    }


class RepositorioTarjetas(Conexion):
    """Medios de pago guardados de cada cliente."""

    def guardar_tarjeta(self, usuario_id: str, numero: str) -> dict[str, Any]:
        """Guarda marca y últimos 4 dígitos; si ya estaba, devuelve la misma."""
        if not luhn_valido(numero):
            raise ErrorPedido("El número de tarjeta no es válido.")
        digitos = solo_digitos(numero)
        marca, ultimos4 = marca_de(digitos), digitos[-4:]
        with self.conectar() as con:
            con.execute(
                "insert into usuarios (id, nombre, email, creado_en) values (?, ?, null, ?) "
                "on conflict(id) do nothing",
                (usuario_id, usuario_id, momento_actual()),
            )
            con.execute(
                "insert into tarjetas (id, usuario_id, marca, ultimos4, creado_en) "
                "values (?, ?, ?, ?, ?) on conflict(usuario_id, marca, ultimos4) do nothing",
                (
                    f"TAR-{uuid.uuid4().hex[:8].upper()}",
                    usuario_id,
                    marca,
                    ultimos4,
                    momento_actual(),
                ),
            )
            fila = con.execute(
                "select * from tarjetas where usuario_id = ? and marca = ? and ultimos4 = ?",
                (usuario_id, marca, ultimos4),
            ).fetchone()
        return _tarjeta(fila)

    def tarjetas_de(self, usuario_id: str) -> list[dict[str, Any]]:
        with self.conectar() as con:
            filas = con.execute(
                "select * from tarjetas where usuario_id = ? order by creado_en desc, rowid desc",
                (usuario_id,),
            ).fetchall()
        return [_tarjeta(f) for f in filas]

    def tarjeta_de(self, usuario_id: str, tarjeta_id: str) -> dict[str, Any] | None:
        """La tarjeta, si es de ese cliente: nadie paga con la tarjeta de otro."""
        with self.conectar() as con:
            fila = con.execute(
                "select * from tarjetas where id = ? and usuario_id = ?", (tarjeta_id, usuario_id)
            ).fetchone()
        return _tarjeta(fila) if fila else None
