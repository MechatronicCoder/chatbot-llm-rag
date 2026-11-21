"""Mensajes de cada sesión (siempre redactados) y métricas por ruta."""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.persistencia.conexion import Conexion
from app.persistencia.esquema import momento_actual


class RepositorioConversacion(Conexion):
    """Historial de la conversación y métricas de uso."""

    def guardar_turno(
        self,
        turno_id: str,
        sesion_id: str,
        usuario_id: str,
        mensaje: str,
        respuesta: str,
        *,
        ruta: str = "",
        fuente: str = "",
        faq_id: str | None = None,
        similitud: float | None = None,
        tokens: tuple[int, int] = (0, 0),
        latencia_ms: int = 0,
        herramientas: list[dict[str, Any]] | None = None,
    ) -> None:
        ahora = momento_actual()
        filas = [
            (
                f"MSG-{uuid.uuid4().hex[:12]}",
                turno_id,
                sesion_id,
                usuario_id,
                "user",
                mensaje,
                ruta,
                fuente,
                faq_id,
                similitud,
                0,
                0,
                0,
                None,
                ahora,
            ),
            (
                f"MSG-{uuid.uuid4().hex[:12]}",
                turno_id,
                sesion_id,
                usuario_id,
                "assistant",
                respuesta,
                ruta,
                fuente,
                faq_id,
                similitud,
                int(tokens[0]),
                int(tokens[1]),
                int(latencia_ms),
                json.dumps(herramientas or [], ensure_ascii=False, default=str),
                ahora,
            ),
        ]
        with self.conectar() as con:
            con.executemany(
                "insert into mensajes (id, turno_id, sesion_id, usuario_id, rol, "
                "contenido_redactado, ruta, fuente, faq_id, similitud, tokens_entrada, "
                "tokens_salida, latencia_ms, herramientas_json, creado_en) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                filas,
            )

    def mensajes_de(self, sesion_id: str, limite: int = 50) -> list[dict[str, Any]]:
        """Últimos `limite` mensajes de la sesión, en orden de inserción."""
        with self.conectar() as con:
            filas = con.execute(
                "select * from mensajes where sesion_id = ? order by rowid desc limit ?",
                (sesion_id, max(0, limite)),
            ).fetchall()
        return [dict(f) for f in reversed(filas)]

    def contar_turnos(self, sesion_id: str) -> int:
        with self.conectar() as con:
            fila = con.execute(
                "select count(*) from mensajes where sesion_id = ? and rol = 'assistant'",
                (sesion_id,),
            ).fetchone()
        return int(fila[0])

    def borrar_sesion(self, sesion_id: str) -> int:
        with self.conectar() as con:
            cursor = con.execute("delete from mensajes where sesion_id = ?", (sesion_id,))
        return int(cursor.rowcount)

    def metricas(self) -> dict[str, Any]:
        with self.conectar() as con:
            filas = con.execute(
                "select ruta, count(*) as turnos, sum(tokens_entrada) as entrada, "
                "sum(tokens_salida) as salida, avg(latencia_ms) as latencia "
                "from mensajes where rol = 'assistant' group by ruta order by turnos desc"
            ).fetchall()
            latencias = [
                int(f[0])
                for f in con.execute(
                    "select latencia_ms from mensajes where rol = 'assistant' "
                    "and latencia_ms > 0 order by latencia_ms"
                ).fetchall()
            ]

        def percentil(valores: list[int], p: float) -> int:
            if not valores:
                return 0
            posicion = min(len(valores) - 1, round((len(valores) - 1) * p))
            return valores[posicion]

        por_ruta = [dict(f) for f in filas]
        return {
            "por_ruta": por_ruta,
            "turnos": sum(f["turnos"] for f in por_ruta),
            "tokens_entrada": sum(f["entrada"] or 0 for f in por_ruta),
            "tokens_salida": sum(f["salida"] or 0 for f in por_ruta),
            "latencia_p50": percentil(latencias, 0.50),
            "latencia_p95": percentil(latencias, 0.95),
        }
