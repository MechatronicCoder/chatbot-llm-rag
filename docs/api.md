# API

La referencia interactiva, generada a partir del código, está en `/docs` (Swagger) y `/redoc`. Este documento resume el contrato que usa el frontend, con ejemplos en PowerShell.

Todas las respuestas son JSON salvo `/api/ventas.csv`. Los identificadores de usuario y sesión aceptan letras, números, `.`, `_`, `-` y `@`, hasta 60 caracteres. Los precios están en la moneda de `datos/tienda.json`, que `/api/health` informa en `tienda.moneda`.

## Chat

`POST /api/chat`

```powershell
$cuerpo = @{ mensaje = "quiero un libro para que mi hijo de 5 años aprenda a sumar"; usuario_id = "cliente-01"; session_id = "cliente-01" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/chat -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($cuerpo))
```

Respuesta (campos principales):

```json
{
  "respuesta": "Para un niño de 5 años te recomiendo «Mis primeras sumas» ...",
  "ruta": "recomendacion",
  "modo": "real",
  "fuente": "LLM + recomendar_libros",
  "herramientas": [{"nombre": "recomendar_libros", "ok": true, "ms": 41, "resumen": "3 recomendaciones"}],
  "libros": [
    {
      "isbn": "9786120000069",
      "titulo": "Mis primeras sumas",
      "autor": "Equipo editorial (ejemplo)",
      "categoria": "educativo",
      "genero": "actividades",
      "edad": "4–6 años",
      "formato": "tapa blanda",
      "precio": 29.9,
      "stock": 25,
      "valoracion": 4.5,
      "num_valoraciones": 60,
      "sinopsis": "Cuaderno de actividades con stickers para aprender a sumar hasta diez jugando...",
      "portada": null,
      "similitud": 0.6123,
      "puntaje": 0.9772,
      "motor": "denso"
    }
  ],
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
  "clasificacion": {"ruta": "llm", "similitud": 0.453, "motivo": "busqueda de libros: 'un libro para'"}
}
```

| Campo | Significado |
|---|---|
| `ruta` | `faq`, `escalar`, `filtro`, `directa`, `herramientas`, `recomendacion`, `accion`, `rag` o `demo` |
| `modo` | `real` (respondió el LLM), `sin_llm` (FAQ, filtro o escalado) o `demo` (sin proveedor) |
| `libros` | Libros que mostró el turno (búsqueda, recomendación o ficha), sin repetidos; el frontend los pinta como tarjetas. `similitud`, `puntaje` y `motor` solo vienen en las recomendaciones |
| `documentos` | Fragmentos usados por el RAG: documento, página o sección, texto y puntaje |
| `tokens` | Tokens de entrada y salida sumando todas las llamadas del turno |
| `turno_id` | Identificador para filtrar el log de este turno |

Errores:

| Código | Cuándo |
|---|---|
| 422 | Mensaje vacío, demasiado largo o identificadores con caracteres no permitidos |
| 429 | Se superó `LIMITE_CHAT_POR_MINUTO`; el encabezado `Retry-After` indica los segundos de espera |

`DELETE /api/sesiones/{session_id}` borra la conversación guardada y devuelve `{session_id, borrados}`.

## Libros, pedidos y documentos

```powershell
Invoke-RestMethod "http://localhost:8000/api/libros?consulta=dahl&limite=5"
Invoke-RestMethod "http://localhost:8000/api/libros?categoria=infantil&edad=6"
Invoke-RestMethod "http://localhost:8000/api/libros/978-612-000004-5"
Invoke-RestMethod "http://localhost:8000/api/categorias"
Invoke-RestMethod "http://localhost:8000/api/pedidos/cliente-01"
Invoke-RestMethod "http://localhost:8000/api/politicas"
```

| Ruta | Respuesta |
|---|---|
| `/api/libros?consulta=&categoria=&autor=&edad=&limite=` | `{total, libros: [...]}` con el resumen de cada libro (mismos campos que `libros` en el chat). `consulta` busca palabras completas en título, autor, editorial, categoría, género, protagonista, temas e ISBN; `edad` devuelve los libros cuyo rango la incluye; `limite` va de 1 a 20 |
| `/api/libros/{isbn}` | Ficha completa con todos los campos del catálogo (ver el README, sección "Catálogo de libros"); acepta el ISBN con guiones; 404 si no existe o fue retirado |
| `/api/categorias` | `[{categoria, libros}]`, de la categoría con más libros a la de menos |
| `/api/pedidos/{usuario_id}` | `{usuario_id, total, pendiente, pagado, pedidos: [{pedido_id, isbn, titulo, autor, cantidad, precio_unitario, total, estado, creado_en}]}` |
| `/api/politicas` | Documentos y cantidad de fragmentos indexados |

Solo se listan libros activos: un libro que desaparece de `datos/catalogo.json` deja de aparecer, pero sus pedidos se conservan.

## Salud

| Ruta | Uso | Respuesta |
|---|---|---|
| `/api/health` | Panel y diagnóstico | Estado, versión, modelo, modo, `tienda` (nombre y moneda), `corpus` (documentos), `catalogo` (libros indexados y motor), FAQs, parámetros y contadores; nunca claves |
| `/api/health/live` | Sonda de vida del contenedor | Siempre 200 si el proceso responde |
| `/api/health/ready` | Sonda de disponibilidad | 200 con los documentos y el catálogo indexados, 503 mientras arranca |

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
| `/api/ventas.csv?usuario_id=` | Pedidos pagados separados por `;`, con las columnas pedido, usuario, isbn, titulo, autor, cantidad, precio_unitario, total, estado y creado_en |
