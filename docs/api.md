# API

La referencia interactiva, generada a partir del código, está en `/docs` (Swagger) y `/redoc`. Este documento resume el contrato que usa el frontend, con ejemplos en PowerShell.

Todas las respuestas son JSON salvo `/api/ventas.csv`. Los identificadores de usuario y sesión aceptan letras, números, `.`, `_`, `-` y `@`, hasta 60 caracteres.

## Chat

`POST /api/chat`

```powershell
$cuerpo = @{ mensaje = "¿tienen laptops?"; usuario_id = "cliente-01"; session_id = "cliente-01" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/chat -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($cuerpo))
```

Respuesta (campos principales):

```json
{
  "respuesta": "Tenemos 3 laptops disponibles: ...",
  "ruta": "herramientas",
  "modo": "real",
  "fuente": "LLM + buscar_productos",
  "herramientas": [{"nombre": "buscar_productos", "ok": true, "ms": 3, "resumen": "3 productos"}],
  "documentos": [],
  "pedidos": [],
  "pagos": [],
  "modelo": "deepseek-chat",
  "endpoint": "principal",
  "latencia_ms": 1840,
  "tokens": [1520, 96],
  "turno": 1,
  "turno_id": "t-3f9a21bc",
  "aviso": null,
  "clasificacion": {"ruta": "llm", "similitud": 0.21, "motivo": "..."}
}
```

| Campo | Significado |
|---|---|
| `ruta` | `faq`, `escalar`, `filtro`, `directa`, `herramientas`, `accion`, `rag` o `demo` |
| `modo` | `real` (respondió el LLM), `sin_llm` (FAQ, filtro o escalado) o `demo` (sin proveedor) |
| `documentos` | Fragmentos usados por el RAG: documento, página, texto y puntaje |
| `tokens` | Tokens de entrada y salida sumando todas las llamadas del turno |
| `turno_id` | Identificador para filtrar el log de este turno |

Errores:

| Código | Cuándo |
|---|---|
| 422 | Mensaje vacío, demasiado largo o identificadores con caracteres no permitidos |
| 429 | Se superó `LIMITE_CHAT_POR_MINUTO`; el encabezado `Retry-After` indica los segundos de espera |

`DELETE /api/sesiones/{session_id}` borra la conversación guardada y devuelve `{session_id, borrados}`.

## Catálogo y pedidos

```powershell
Invoke-RestMethod "http://localhost:8000/api/productos?consulta=monitor&limite=5"
Invoke-RestMethod "http://localhost:8000/api/pedidos/cliente-01"
Invoke-RestMethod "http://localhost:8000/api/politicas"
```

- `/api/productos`: `{total, productos: [{codigo, nombre, categoria, precio, specs, stock}]}`; filtros `consulta`, `categoria` y `limite` (1 a 20).
- `/api/pedidos/{usuario_id}`: `{usuario_id, total, pendiente, pagado, pedidos: [...]}`.
- `/api/politicas`: documentos y cantidad de fragmentos indexados.

## Salud

| Ruta | Uso | Respuesta |
|---|---|---|
| `/api/health` | Panel y diagnóstico | Estado, versión, modelo, modo, corpus, FAQs, parámetros y contadores; nunca claves |
| `/api/health/live` | Sonda de vida del contenedor | Siempre 200 si el proceso responde |
| `/api/health/ready` | Sonda de disponibilidad | 200 con el corpus indexado, 503 mientras arranca |

## Administración

Si `ADMIN_TOKEN` está definido, estas rutas exigen el encabezado `X-Admin-Token` y responden 401 sin él.

```powershell
$h = @{ "X-Admin-Token" = $env:ADMIN_TOKEN }
Invoke-RestMethod "http://localhost:8000/api/logs?n=20&nivel=WARN" -Headers $h
Invoke-RestMethod "http://localhost:8000/api/logs?turno=t-3f9a21bc" -Headers $h
Invoke-RestMethod "http://localhost:8000/api/metricas" -Headers $h
Invoke-RestMethod "http://localhost:8000/api/sesion/cliente-01" -Headers $h
Invoke-WebRequest "http://localhost:8000/api/ventas.csv" -Headers $h -OutFile ventas.csv
```

| Ruta | Respuesta |
|---|---|
| `/api/logs?n=&nivel=&turno=` | `{nivel_minimo, total, eventos}`, del más nuevo al más viejo |
| `/api/metricas` | Turnos por ruta, tokens totales, latencia p50 y p95 |
| `/api/sesion/{session_id}` | Mensajes guardados (siempre redactados) |
| `/api/ventas.csv` | Pedidos pagados separados por `;` |
