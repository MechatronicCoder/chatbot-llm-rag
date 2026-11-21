"""Registro de eventos por turno: consola (texto o JSON) y archivo JSONL.

Regla de diseño: un dato, un dueño.

- Lo que se consulta, filtra o suma (pedidos, stock, tokens, latencia) vive en
  la base de datos; el log solo lo repite en la línea de cierre del turno.
- Lo que explica qué pasó (un filtro que saltó, una caída a modo demo, una
  herramienta lenta) vive en el log.
- El valor original de una tarjeta, un CVV o una clave no va a ningún destino:
  se enmascara antes de tocar la base y el log.

Cada evento lleva el `turno_id`, de modo que un turno se reconstruye filtrando
por ese campo. En Azure se usa `LOG_FORMATO=json`: cada línea de consola es un
JSON que Log Analytics indexa sin configuración adicional.
"""

from __future__ import annotations

import json
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from app.core.config import Settings

NIVELES = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def nuevo_turno() -> str:
    """Identificador corto y legible para seguir un turno en el log."""
    return f"t-{uuid.uuid4().hex[:8]}"


class RegistroEventos:
    """Escribe eventos en consola y en un archivo JSONL con rotación por tamaño."""

    def __init__(
        self,
        archivo: Path | None,
        *,
        nivel: str = "INFO",
        consola: bool = True,
        formato: str = "texto",
        rotacion_mb: float = 2.0,
        salida: TextIO | None = None,
    ) -> None:
        self.archivo = archivo
        self.nivel = nivel.upper()
        self.consola = consola
        self.formato = formato
        self.rotacion_bytes = int(rotacion_mb * 1024 * 1024)
        self._salida = salida
        self._candado = threading.Lock()

    @classmethod
    def desde_config(cls, ajustes: Settings) -> RegistroEventos:
        return cls(
            ajustes.log_archivo,
            nivel=ajustes.log_nivel,
            consola=ajustes.log_consola,
            formato=ajustes.log_formato,
            rotacion_mb=ajustes.log_rotacion_mb,
        )

    # -- escritura -----------------------------------------------------------
    def _permite(self, nivel: str) -> bool:
        return NIVELES.get(nivel, 20) >= NIVELES.get(self.nivel, 20)

    def _rotar(self, archivo: Path) -> None:
        """Si el archivo supera el límite, se guarda como `.1` y se empieza de cero."""
        try:
            if archivo.exists() and archivo.stat().st_size >= self.rotacion_bytes:
                anterior = archivo.with_suffix(archivo.suffix + ".1")
                anterior.unlink(missing_ok=True)
                archivo.rename(anterior)
        except OSError:
            pass  # el log nunca debe tumbar un turno

    def evento(self, nivel: str, nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
        """Registra un evento y lo devuelve como diccionario."""
        nivel = nivel.upper()
        registro: dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "nivel": nivel,
            "evento": nombre,
            "turno": turno,
            **campos,
        }
        if not self._permite(nivel):
            return registro

        if self.consola:
            salida = self._salida or sys.stdout
            if self.formato == "json":
                linea = json.dumps(registro, ensure_ascii=False, default=str)
            else:
                detalle = " ".join(f"{k}={v}" for k, v in campos.items() if v not in (None, ""))
                linea = f"{registro['ts'][11:23]} {nivel:<5} {nombre:<22} turno={turno or '-'} "
                linea = (linea + detalle).rstrip()
            print(linea, file=salida, flush=True)

        if self.archivo is not None:
            with self._candado:
                try:
                    self.archivo.parent.mkdir(parents=True, exist_ok=True)
                    self._rotar(self.archivo)
                    with self.archivo.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
                except OSError:
                    pass
        return registro

    def info(self, nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
        return self.evento("INFO", nombre, turno, **campos)

    def aviso(self, nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
        return self.evento("WARN", nombre, turno, **campos)

    def error(self, nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
        return self.evento("ERROR", nombre, turno, **campos)

    def depuracion(self, nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
        return self.evento("DEBUG", nombre, turno, **campos)

    # -- lectura (panel de logs y /api/logs) ---------------------------------
    def leer(
        self, limite: int = 100, nivel: str | None = None, turno: str | None = None
    ) -> list[dict[str, Any]]:
        """Últimos eventos del archivo, del más nuevo al más viejo, con filtros."""
        if self.archivo is None or not self.archivo.exists():
            return []
        with self._candado:
            try:
                lineas = self.archivo.read_text(encoding="utf-8").splitlines()[-2000:]
            except OSError:
                return []

        piso = NIVELES.get((nivel or "").upper(), 0)
        salida: list[dict[str, Any]] = []
        for linea in reversed(lineas):
            if not linea.strip():
                continue
            try:
                registro = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if piso and NIVELES.get(str(registro.get("nivel")), 0) < piso:
                continue
            if turno and registro.get("turno") != turno:
                continue
            salida.append(registro)
            if len(salida) >= max(1, limite):
                break
        return salida
