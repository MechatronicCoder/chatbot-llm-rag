# API

La referencia interactiva, generada a partir del código, está en `/docs` (Swagger) y `/redoc`. Este documento resume el contrato que usa el frontend, con ejemplos en PowerShell.

Todas las respuestas son JSON salvo `/api/ventas.csv`. Los identificadores de usuario y sesión aceptan letras, números, `.`, `_`, `-` y `@`, hasta 60 caracteres. Los precios están en la moneda de `datos/tienda.json`, que `/api/health` informa en `tienda.moneda`.

## Acceso

Con `ACCESO_USUARIO` y `ACCESO_CLAVE_HASH` configurados (obligatorios en producción), todas las rutas salvo `/api/health*` y `/api/acceso/*` responden `401` con el encabezado `X-Acceso: requerido` hasta iniciar sesión. Sin esas variables, en local, la API queda abierta.

| Ruta | Respuesta |
|---|---|
| `GET /api/acceso/estado` | `{requerido, autenticado, usuario}` |
| `POST /api/acceso/login` | Con `{usuario, clave}` correctos: `{requerido: true, autenticado: true, usuario}` y la cookie de sesión `acceso_sesion` (HttpOnly, SameSite=Strict, Secure en producción, 8 horas). Si no: `401` con "Usuario o clave incorrectos.", sin decir cuál falló |
| `POST /api/acceso/logout` | Cierra la sesión y borra la cookie |

Los intentos de inicio de sesión se limitan a 5 por IP cada 15 minutos (`ACCESO_INTENTOS_POR_IP`) y a 100 en total; al superarlos responde `429` con `Retry-After`.

Desde PowerShell, la sesión se guarda en una variable y se reutiliza en las demás llamadas:

```powershell
$credenciales = @{ usuario = (Read-Host "Usuario"); clave = (Read-Host "Clave") } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/acceso/login -ContentType "application/json" -Body $credenciales -SessionVariable sesion
Invoke-RestMethod "http://localhost:8000/api/libros?limite=3" -WebSession $sesion
```

## Chat

`POST /api/chat`

```powershell
$cuerpo = @{ mensaje = "busco algo de terror para mi sobrino de 13 años"; usuario_id = "cliente-01"; session_id = "cliente-01" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/chat -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($cuerpo))
```

Respuesta (campos principales):

```json
{
  "respuesta": "Para un lector de 13 años te recomiendo «El bisturí», de La calle del terror ...",
  "ruta": "recomendacion",
  "modo": "real",
  "fuente": "LLM + recomendar_libros",
  "herramientas": [{"nombre": "recomendar_libros", "ok": true, "ms": 41, "resumen": "3 recomendaciones"}],
  "libros": [
    {
      "isbn": "9788440662286",
      "titulo": "El bisturí (La calle del terror)",
      "autor": "R. L. Stine",
      "categoria": "juvenil",
      "genero": "terror",
      "edad": "12–17 años",
      "formato": "tapa blanda",
      "precio": 75.33,
      "stock": 11,
      "valoracion": 3.8,
      "num_valoraciones": 6,
      "sinopsis": "Laurie trabaja como voluntaria en el hospital de Shadyside y descubre que cada habitación esconde un secreto inquietante...",
      "portada": null,
      "similitud": 0.5834,
      "puntaje": 0.8731,
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
  "clasificacion": {"ruta": "llm", "similitud": 0.453, "motivo": "busqueda de libros: 'busco'"}
}
```

| Campo | Significado |
|---|---|
| `ruta` | `faq`, `escalar`, `filtro`, `directa`, `herramientas`, `recomendacion`, `accion`, `rag` o `demo` |
| `modo` | `real` (respondió el LLM), `sin_llm` (FAQ, filtro o escalado) o `demo` (sin proveedor) |
| `libros` | Libros que mostró el turno (búsqueda, recomendación o ficha), sin repetidos; el frontend los pinta como tarjetas. `similitud`, `puntaje` y `motor` solo vienen en las recomendaciones |
| `carrito` | El último pedido o carrito que tocó el turno, con la misma forma que en `/api/pedidos`; `null` si el turno no tocó ninguno. Los montos los calcula el sistema |
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
Invoke-RestMethod "http://localhost:8000/api/libros?consulta=christie&limite=5"
Invoke-RestMethod "http://localhost:8000/api/libros?categoria=juvenil&edad=13"
Invoke-RestMethod "http://localhost:8000/api/libros/978-84-17826-89-5"
Invoke-RestMethod "http://localhost:8000/api/categorias"
Invoke-RestMethod "http://localhost:8000/api/pedidos/cliente-01"
Invoke-RestMethod "http://localhost:8000/api/politicas"
```

| Ruta | Respuesta |
|---|---|
| `/api/libros?consulta=&categoria=&autor=&edad=&limite=` | `{total, libros: [...]}` con el resumen de cada libro (mismos campos que `libros` en el chat). `consulta` busca palabras completas en título, autor, editorial, categoría, género, protagonista, temas e ISBN; `edad` devuelve los libros cuyo rango la incluye; `limite` va de 1 a 20 |
| `/api/libros/{isbn}` | Ficha completa con todos los campos del catálogo (ver el README, sección "Catálogo de libros"); acepta el ISBN con guiones; 404 si no existe o fue retirado |
| `/api/categorias` | `[{categoria, libros}]`, de la categoría con más libros a la de menos |
| `/api/pedidos/{usuario_id}` | `{usuario_id, total, pendiente, pagado, pedidos: [...]}`, del más nuevo al más viejo. Cada pedido trae `pedido_id`, `estado` (`carrito`, `reservado`, `pagado` o `cancelado`), `zona` y `zona_nombre` del envío, `lineas` (`isbn`, `titulo`, `autor`, `cantidad`, `precio_unitario`, `importe`), `unidades`, `subtotal`, `envio` (`null` mientras falte la zona), `total`, `falta_para_envio_gratis` y `creado_en`. Un carrito vacío no se lista |
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
| `/api/metricas` | Turnos por ruta, tokens totales y de las últimas 24 horas (`tokens_24h`, lo que mide el tope `LLM_TOKENS_POR_DIA`), latencia p50 y p95 |
| `/api/sesion/{session_id}` | Mensajes guardados (siempre redactados) |
| `/api/ventas.csv?usuario_id=` | Una fila por libro vendido en pedidos pagados, separada por `;`, con las columnas pedido, usuario, isbn, titulo, autor, cantidad, precio_unitario, importe, envio_pedido, total_pedido, estado y creado_en |
