"""Cliente de chat para cualquier API compatible con OpenAI (DeepSeek, Qwen, etc.).

Qué resuelve:

1. Reintentos: un 429, un 5xx o un corte de red se repiten con espera creciente
   antes de dar por caído al proveedor (ver `reintentos.py`).
2. Cadena de respaldo: si el proveedor principal falla (clave inválida, cuota,
   modelo inexistente, error del servidor, sin red), se intenta el siguiente.
3. Herramientas (function calling) en el formato estándar `tools`.
4. Respuesta normalizada: texto, llamadas a herramientas con argumentos ya
   convertidos a diccionario, tokens usados y latencia.

Si no hay proveedores configurados se lanza `SinProveedores` de inmediato: el
servicio de chat lo usa para pasar a modo demo sin intentar ninguna llamada.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.logs import RegistroEventos
from app.llm.reintentos import (
    FalloPasajero,
    PoliticaReintentos,
    detalle_error,
    post_con_reintentos,
)

Mensaje = dict[str, Any]


class SinProveedores(RuntimeError):
    """No hay proveedor configurado o todos fallaron."""

    def __init__(self, mensaje: str, intentos: Sequence[str] = ()) -> None:
        super().__init__(mensaje)
        self.intentos = list(intentos)


@dataclass(frozen=True)
class Proveedor:
    """Un destino concreto: URL base, clave y modelo."""

    nombre: str
    base_url: str
    api_key: str
    modelo: str

    @property
    def completo(self) -> bool:
        return bool(self.base_url and self.api_key and self.modelo)


@dataclass
class RespuestaLLM:
    """Respuesta normalizada, venga del proveedor que venga."""

    texto: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    razonamiento: str = ""
    modelo: str = ""
    proveedor: str = ""
    uso: dict[str, int] = field(default_factory=dict)
    latencia_s: float = 0.0


class ModeloChat(Protocol):
    """Lo que el servicio de chat necesita de un modelo (permite usar dobles en pruebas)."""

    @property
    def configurado(self) -> bool: ...

    @property
    def modelo_principal(self) -> str: ...

    def chat(
        self,
        mensajes: list[Mensaje],
        herramientas: Sequence[dict[str, Any]] | None = None,
        *,
        temperatura: float = 0.3,
        max_tokens: int = 700,
    ) -> RespuestaLLM: ...


def _argumentos(crudo: Any) -> dict[str, Any]:
    """Los argumentos llegan como texto JSON; si vienen mal formados, se usa {}."""
    if isinstance(crudo, dict):
        return crudo
    try:
        valor = json.loads(crudo or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return valor if isinstance(valor, dict) else {}


# Un timeout del chat no se repite con el mismo proveedor: ya se esperó el plazo
# entero y el de respaldo suele ser más rápido.
POLITICA_CHAT = PoliticaReintentos(reintentar_timeouts=False)


class ClienteLLM:
    """Llama a `/chat/completions` del primer proveedor que responda."""

    def __init__(
        self,
        proveedores: Sequence[Proveedor],
        *,
        timeout_s: float = 60.0,
        extra_body: dict[str, Any] | None = None,
        transporte: httpx.BaseTransport | None = None,
        politica: PoliticaReintentos = POLITICA_CHAT,
        esperar: Callable[[float], None] = time.sleep,
        registro: RegistroEventos | None = None,
    ) -> None:
        self.proveedores = [p for p in proveedores if p.completo]
        self.extra_body = dict(extra_body or {})
        self.politica = politica
        self._esperar = esperar
        self._registro = registro
        self._http = httpx.Client(timeout=timeout_s, transport=transporte)

    @classmethod
    def desde_config(cls, ajustes: Settings, registro: RegistroEventos | None = None) -> ClienteLLM:
        proveedores = [
            Proveedor(
                "principal",
                ajustes.llm_base_url,
                ajustes.llm_api_key.get_secret_value(),
                ajustes.llm_model,
            ),
            Proveedor(
                "respaldo",
                ajustes.llm_2_base_url,
                ajustes.llm_2_api_key.get_secret_value(),
                ajustes.llm_2_model,
            ),
        ]
        politica = PoliticaReintentos(
            reintentos=ajustes.api_reintentos,
            espera_max_s=ajustes.api_espera_max_s,
            reintentar_timeouts=False,
        )
        return cls(
            proveedores,
            timeout_s=ajustes.llm_timeout_s,
            extra_body=ajustes.llm_extra_body,
            politica=politica,
            registro=registro,
        )

    @property
    def configurado(self) -> bool:
        return bool(self.proveedores)

    @property
    def modelo_principal(self) -> str:
        return self.proveedores[0].modelo if self.proveedores else "sin modelo"

    def cerrar(self) -> None:
        self._http.close()

    # -- llamada ---------------------------------------------------------------
    def chat(
        self,
        mensajes: list[Mensaje],
        herramientas: Sequence[dict[str, Any]] | None = None,
        *,
        temperatura: float = 0.3,
        max_tokens: int = 700,
    ) -> RespuestaLLM:
        if not self.proveedores:
            raise SinProveedores(
                "Sin modelo configurado: definir LLM_BASE_URL, LLM_API_KEY y LLM_MODEL."
            )

        intentos: list[str] = []
        for proveedor in self.proveedores:
            payload: dict[str, Any] = {
                "model": proveedor.modelo,
                "messages": mensajes,
                "temperature": temperatura,
                "max_tokens": max_tokens,
                **self.extra_body,
            }
            if herramientas:
                payload["tools"] = [{"type": "function", "function": h} for h in herramientas]
                payload["tool_choice"] = "auto"

            inicio = time.perf_counter()
            try:
                respuesta = post_con_reintentos(
                    self._http,
                    f"{proveedor.base_url}/chat/completions",
                    cuerpo=payload,
                    cabeceras={"Authorization": f"Bearer {proveedor.api_key}"},
                    politica=self.politica,
                    esperar=self._esperar,
                    al_reintentar=self._avisar_reintento(proveedor),
                )
            except FalloPasajero as exc:
                intentos.append(f"{proveedor.nombre}: {exc}")
                continue

            if respuesta.status_code != 200:
                intentos.append(
                    f"{proveedor.nombre}: HTTP {respuesta.status_code} {detalle_error(respuesta)}"
                )
                continue

            try:
                normalizada = self._normalizar(
                    respuesta.json(), proveedor, time.perf_counter() - inicio
                )
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                intentos.append(f"{proveedor.nombre}: respuesta inválida ({type(exc).__name__})")
                continue
            if intentos and self._registro is not None:
                self._registro.aviso("llm.respaldo", proveedor=proveedor.nombre, fallos=intentos)
            return normalizada

        raise SinProveedores("Ningún proveedor respondió: " + "; ".join(intentos), intentos)

    def _avisar_reintento(self, proveedor: Proveedor) -> Callable[[str, float], None] | None:
        registro = self._registro
        if registro is None:
            return None

        def avisar(motivo: str, espera: float) -> None:
            registro.aviso(
                "llm.reintento",
                proveedor=proveedor.nombre,
                motivo=motivo,
                espera_s=round(espera, 1),
            )

        return avisar

    @staticmethod
    def _normalizar(datos: dict[str, Any], proveedor: Proveedor, latencia: float) -> RespuestaLLM:
        mensaje = datos["choices"][0]["message"]
        llamadas = [
            {
                "id": tc.get("id") or f"call-{i}",
                "name": tc["function"]["name"],
                "arguments": _argumentos(tc["function"].get("arguments")),
            }
            for i, tc in enumerate(mensaje.get("tool_calls") or [])
        ]
        uso = datos.get("usage") or {}
        return RespuestaLLM(
            texto=mensaje.get("content") or "",
            tool_calls=llamadas,
            razonamiento=mensaje.get("reasoning_content") or "",
            modelo=datos.get("model") or proveedor.modelo,
            proveedor=proveedor.nombre,
            uso={
                "prompt_tokens": int(uso.get("prompt_tokens") or 0),
                "completion_tokens": int(uso.get("completion_tokens") or 0),
            },
            latencia_s=latencia,
        )
