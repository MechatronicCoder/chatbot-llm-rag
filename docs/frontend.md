# Guía del frontend

El frontend del librero es una página estática: HTML, CSS y JavaScript con módulos ES nativos, sin framework y sin paso de compilación. El backend sirve la carpeta `frontend/` en `/` mediante `StaticFiles(html=True)`, por lo que `index.html` se entrega en la raíz y todas las llamadas a la API son del mismo origen, con rutas absolutas (`/api/...`).

La interfaz tiene tres zonas:

| Zona | Contenido |
|---|---|
| Conversación | Hilo de mensajes, tarjetas de libros, chips de metadatos, respuesta completa en JSON, formulario de envío |
| Panel del cliente | ID del cliente, recargar, borrar conversación, pedidos con totales, catálogo de libros con búsqueda |
| Log en vivo | Eventos del backend con filtros por nivel y por turno, línea de métricas, token de administración |

## Estructura de archivos

```
frontend/
├── index.html          estructura de la página (sin estilos ni scripts en línea)
├── css/
│   ├── base.css          variables y estilos base
│   ├── disposicion.css   columnas y controles
│   ├── chat.css          hilo, burbujas, tarjetas de libros, chips y formulario
│   ├── paneles.css       panel lateral y panel de logs
│   ├── responsive.css    ajustes por ancho de pantalla
│   └── animaciones.css   animaciones y preferencias del sistema
└── js/
    ├── main.js         arranque, estado de la cabecera y temporizadores
    ├── api.js          cliente HTTP, ErrorApi y token de administración
    ├── util.js         escape de HTML, formato, localStorage seguro, ayudas de DOM
    ├── chat.js         hilo, burbujas, chips, indicador de escritura, envío
    ├── libros.js       tarjetas de los libros de cada respuesta
    ├── panel.js        cliente, pedidos y catálogo de libros
    └── logs.js         log en vivo, filtros, métricas y formulario de token
```

### Responsabilidad de cada módulo

| Módulo | Responsabilidad | Exporta |
|---|---|---|
| `util.js` | Funciones puras y ayudas de DOM. No depende de ningún otro módulo. | `escaparHtml`, `formatear`, `ponerHtmlFormateado`, `leerLocal`, `guardarLocal`, `borrarLocal`, `CLAVES`, `crear`, `porId`, `vaciar`, `ponerTexto`, `prefiereMenosMovimiento`, `cercaDelFinal`, `desplazarA`, `irAlFinal`, `haySeleccionDentro`, moneda (`configurarMoneda`), formateadores (`formatearPrecio`, `formatearMs`, `formatearHora`, `formatearDuracion`, `formatearPuntaje`, `formatearCantidad`, `plural`, `resumir`) |
| `api.js` | Una función por endpoint, tiempo límite, cancelación, traducción de errores HTTP a mensajes legibles, cabecera `X-Admin-Token`. | `ErrorApi`, `segundosDeRetryAfter`, `salud`, `buscarLibros`, `pedidosDe`, `enviarMensaje`, `borrarSesion`, `leerLogs`, `leerMetricas`, `obtenerTokenAdmin`, `guardarTokenAdmin`, `borrarTokenAdmin` |
| `chat.js` | Todo lo que ocurre dentro del hilo y del formulario de envío. | `iniciarChat({ obtenerUsuario, alFiltrarTurno, alResponder })` → `{ enviar, limpiar, anotar, marcarTurno }` |
| `libros.js` | Tarjetas de los libros de una respuesta: título, autor, precio, stock, valoración y edad, siempre como texto. | `tarjetasDeLibros(libros)` → `<ul>` o `null` |
| `panel.js` | Columna del cliente: id (guardado en localStorage), recargar, borrar conversación, pedidos y catálogo. | `iniciarPanel({ alCambiarUsuario, alRecargar, alBorrar })` → `{ usuario, cargarPedidos, cargarCatalogo }` |
| `logs.js` | Panel de logs: refresco pausable, filtros, métricas, formulario de token ante 401. | `iniciarLogs({ alCambiarTurno })` → `{ refrescar, refrescarSiActivo, filtrarTurno, turnoActual }` |
| `main.js` | Crea los tres módulos, los conecta mediante callbacks, pinta la cabecera desde `/api/health` (incluidos el nombre de la tienda, el título de la pestaña y la moneda) y mantiene los temporizadores. | (nada; es el punto de entrada) |

### Dependencias

```
main.js   ─► chat.js, panel.js, logs.js, api.js, util.js
chat.js   ─► libros.js, api.js, util.js
panel.js  ─► api.js, util.js
logs.js   ─► api.js, util.js
libros.js ─► util.js
api.js    ─► util.js
```

Los módulos de interfaz (chat, panel y logs) no se importan entre sí; `libros.js` solo construye las tarjetas que usa el chat. Cuando el chat necesita filtrar el log, llama a `alFiltrarTurno`, y `main.js` lo redirige a `logs.filtrarTurno`. Así cada módulo se puede leer y modificar sin conocer a los demás.

## Ver los cambios en vivo

No hay compilación: cada archivo se sirve tal cual desde el disco.

| Forma de arranque | Comando | Qué hacer tras editar |
|---|---|---|
| Contenedores | `docker compose up` | La carpeta `frontend/` está montada en el contenedor; basta guardar y recargar con F5. |
| Proceso local | `uv run uvicorn app.main:crear_app --factory --reload` | Abrir `http://localhost:8000` y recargar con F5. `--reload` vigila el código Python; los estáticos se leen en cada petición, así que no requieren reinicio. |

Notas:

- Si un módulo no refleja el cambio, se fuerza la recarga con Ctrl+F5 (Cmd+Shift+R en macOS) o se activa "Disable cache" con las herramientas de desarrollo abiertas.
- `index.html` no funciona abierto como archivo (`file://`): los módulos ES exigen HTTP y la API debe estar en el mismo origen.
- Si la consola muestra `Failed to load module script ... MIME type of "text/plain"`, el servidor está tomando el tipo de `.js` del registro de Windows. La corrección corresponde al backend: registrar `mimetypes.add_type("text/javascript", ".js")` antes de montar `StaticFiles`.

## Arranque y temporizadores

`main.js` ejecuta, en orden:

1. `iniciarPanel`, `iniciarLogs` e `iniciarChat`, conectando sus callbacks.
2. Primera consulta a `/api/health` y carga en paralelo de catálogo, pedidos y logs.
3. Un intervalo de 3 s que llama a `logs.refrescarSiActivo()`.
4. Un oyente de `visibilitychange` que refresca logs y salud al volver a la pestaña.

La consulta de salud se reprograma a sí misma según el resultado:

| Resultado de `/api/health` | Indicador en la cabecera | Próxima consulta |
|---|---|---|
| `estado: "ok"` | En línea (punto verde) | 10 s |
| `estado: "iniciando"` o HTTP 503 | Iniciando… (punto ámbar con latido) | 1,5 s |
| Error de red u otro HTTP | Sin conexión (punto rojo) | 1,5 s, 3 s, 6 s, 12 s… hasta 15 s |

Cuando el estado pasa a `"ok"` después de haber sido otro, se recargan catálogo, pedidos y logs, porque las cargas iniciales pudieron fallar mientras el servidor arrancaba. Cada respuesta del chat también dispara una consulta de salud (para actualizar los contadores), recarga pedidos y catálogo y refresca el log si no está en pausa.

## Contrato de la API

Todas las rutas son del mismo origen. Las respuestas son JSON.

| Método y ruta | Función en `api.js` | Uso en la interfaz |
|---|---|---|
| `GET /api/health` | `salud()` | Cabecera: nombre de la tienda, moneda, estado, modelo, modo, corpus, contadores de libros y ejemplares, versión |
| `GET /api/libros?consulta=&categoria=&limite=8` | `buscarLibros({ consulta, categoria, limite })` | Catálogo de libros y búsqueda por título, autor o ISBN (con cancelación de la búsqueda anterior) |
| `GET /api/pedidos/{usuario_id}` | `pedidosDe(usuarioId)` | Lista de pedidos y totales pendiente/pagado |
| `POST /api/chat` | `enviarMensaje({ mensaje, usuarioId, sessionId })` | Turno de conversación; `session_id` es el mismo id del cliente |
| `DELETE /api/sesiones/{session_id}` | `borrarSesion(sessionId)` | Botón "Borrar conversación" |
| `GET /api/logs?n=60&nivel=&turno=` | `leerLogs({ n, nivel, turno })` | Panel de logs (administración) |
| `GET /api/metricas` | `leerMetricas()` | Línea de métricas (administración) |

Los endpoints de administración envían `X-Admin-Token` cuando hay un token guardado. Si el servidor no tiene token configurado, la cabecera simplemente se ignora.

### Respuesta de `/api/chat` y chips

| Campo | Chip o elemento |
|---|---|
| `respuesta` | Texto de la burbuja (con `**negrita**` y saltos de línea) |
| `aviso` | Nota ámbar bajo el texto |
| `libros[]` | Una tarjeta por libro: título, autor y una línea con precio, stock (o "agotado"), valoración con votos y edad; la sinopsis va en el `title` |
| `ruta`, `fuente` | Chip `ruta …` (la fuente va en el `title`) |
| `modo` | Chip `modo …` (ámbar si es `demo`) |
| `latencia_ms` | Chip `N ms` |
| `modelo`, `endpoint` | Chip con el modelo (el endpoint va en el `title`) |
| `tokens` `[entrada, salida]` | Chip `tokens E/S` |
| `faq_id`, `similitud` | Chip `FAQ id · similitud` |
| `herramientas[]` | Un chip por herramienta: `nombre N ms`; rojo si `ok` es `false`; resumen y argumentos en el `title` |
| `documentos[]` | Un chip por documento: `nombre · pág. N · score` en un PDF o `nombre · secc. N · score` en un Markdown, donde cada sección cuenta como página; fragmento de texto en el `title` |
| `pedidos[]`, `pagos[]` | Un chip verde por id |
| `turno`, `turno_id` | Chip clicable `turno N · id` que filtra el log por `turno_id` |
| (respuesta completa) | Bloque `<details>` "Ver respuesta completa" con el JSON formateado |

### Errores

| Situación | `ErrorApi` | Comportamiento en la interfaz |
|---|---|---|
| Sin conexión | `status: 0`, `tipo: 'red'` | Burbuja: "No se pudo conectar con el servidor…"; el texto vuelve a la caja de mensaje |
| Tiempo agotado (15 s; 120 s en el chat) | `status: 0`, `tipo: 'tiempo'` | Burbuja con aviso de demora |
| 422 | `mensaje` = `detail` del backend | Burbuja: "El mensaje no se pudo procesar: …". Se aceptan `detail` como texto o como lista de errores de validación (`loc` y `msg`) |
| 429 | `reintentarEn` = segundos de `Retry-After` | Burbuja: "Se alcanzó el límite de mensajes por minuto, intenta en N s." y el botón muestra una cuenta regresiva |
| 401 o 403 en logs/métricas | `sinPermiso: true` | El panel de logs muestra el formulario del token en lugar de un error |
| 503 | — | Se trata como "iniciando" en la cabecera |
| Otros 5xx | — | "El servidor tuvo un problema (N)…" |

`Retry-After` se interpreta tanto en segundos como en fecha HTTP.

## Content-Security-Policy

El backend envía una cabecera `Content-Security-Policy`, configurable con la variable `CONTENT_SECURITY_POLICY` del `.env`. Las directivas que condicionan el frontend son, como mínimo:

```
script-src 'self'
style-src 'self' https://fonts.googleapis.com
```

Ninguna incluye `'unsafe-inline'` ni `'unsafe-eval'`. Por eso:

| Permitido | No permitido |
|---|---|
| `<script type="module" src="js/...">` del mismo origen | `<script>` con código en línea |
| `<link rel="stylesheet" href="css/...">` | Bloques `<style>` |
| `addEventListener` | Atributos `onclick=""`, `oninput=""` y similares |
| Cambiar clases, `hidden`, `data-*`, `aria-*` | Atributos `style=""` en el HTML o `setAttribute('style', …)` |
| Hojas de estilo de `fonts.googleapis.com` | URLs `javascript:` |
| Modificar estilos por CSSOM (`elemento.style.setProperty`), aunque el proyecto prefiere clases | `eval`, `new Function`, `setTimeout('texto')` |

El motivo es de seguridad: una política sin `'unsafe-inline'` neutraliza la mayoría de los ataques XSS, porque aunque un HTML inyectado llegara al DOM, sus scripts y manejadores en línea no se ejecutarían. Mantener el código sin nada en línea permite conservar la política estricta. `util.crear()` refuerza la regla: lanza un error si se le pasa un atributo `style` u `on*`.

### Agregar una fuente o una librería externa

Cualquier recurso de otro origen que la política por defecto no contemple exige ajustar `CONTENT_SECURITY_POLICY` en el `.env` y reiniciar el backend. La política por defecto (definida en `app/core/config.py`) es:

```dotenv
CONTENT_SECURITY_POLICY=default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Casos habituales:

- Google Fonts: ya está cubierto. La hoja de estilos viene de `fonts.googleapis.com` (`style-src`) y los archivos de fuente de `fonts.gstatic.com` (`font-src`). Basta un `<link rel="stylesheet">` en `index.html`, nunca un `@import` dentro de un bloque `<style>`.
- Otro proveedor de fuentes: su host se agrega a `style-src` y a `font-src`.
- Librería desde un CDN: su host se agrega a `script-src`. Es preferible copiar el archivo a `frontend/js/vendor/` y mantener solo `'self'`.
- Imágenes externas: su host se agrega a `img-src`.
- Peticiones a otro origen: el host se agrega a `connect-src` (hoy no hace falta, todo es del mismo origen).

Al sobrescribir la variable se reemplaza la política completa, de modo que se parte de la política por defecto y se agrega solo lo necesario.

Cuando algo queda bloqueado, la consola del navegador muestra un mensaje `Refused to …` que nombra la directiva responsable.

## Estilos

Los estilos están en `css/`, en seis hojas que `index.html` carga en este orden: `base.css` (variables y base), `disposicion.css` (columnas y controles), `chat.css`, `paneles.css` (panel lateral y logs), `responsive.css` y `animaciones.css` (incluye movimiento reducido y colores forzados). El orden importa: las reglas responsive se cargan después de los componentes para prevalecer sobre sus reglas base. Ningún archivo de código supera las 400 líneas.

### Variables

| Grupo | Ejemplos | Uso |
|---|---|---|
| Superficies y texto | `--color-fondo`, `--color-panel`, `--color-linea`, `--color-texto`, `--color-tenue` | Fondo, paneles, bordes y texto secundario |
| Acento y semánticos | `--color-acento`, `--color-ok`, `--color-aviso`, `--color-error`, `--color-ruta` | Fondos y bordes |
| Variantes para texto | `--color-acento-texto`, `--color-ok-texto`, `--color-aviso-texto`, `--color-error-texto` | Texto pequeño sobre fondo oscuro con contraste AA |
| Logs | `--color-fondo-log`, `--color-hora-log`, `--color-info-log` … | Panel de logs |
| Tipografía | `--fuente-base`, `--fuente-mono`, `--texto-xs` … `--texto-l` | Fuentes del sistema, sin descargas |
| Radios | `--radio-s`, `--radio-m`, `--radio-l`, `--radio-pildora` | Bordes redondeados |
| Espacios | `--esp-1` (4 px) … `--esp-5` (20 px) | Márgenes y separaciones |
| Columnas | `--ancho-panel`, `--ancho-logs`, `--ancho-lateral-compacto`, `--ancho-burbuja` | Disposición |
| Movimiento | `--dur-*`, `--curva-*`, `--retardo-chips`, `--paso-chip`, `--desplazamiento-entrada` | Animaciones |

Los componentes no usan colores literales: si hace falta un tono nuevo, se define una variable en `:root`.

### Disposición

| Ancho | Columnas | Comportamiento |
|---|---|---|
| 1280 px o más | Chat · panel (320 px) · logs (380 px) | Página de alto fijo; cada columna se desplaza por dentro |
| 1000 a 1279 px | Chat · columna derecha (340 px) con panel arriba y logs abajo | Igual, alto fijo |
| Menos de 1000 px | Una columna: chat (78 % del alto), panel, logs (70 % del alto) | La página se desplaza; no hay desplazamiento horizontal |

Los elementos de la grilla llevan `min-width: 0` y los textos largos usan `overflow-wrap: anywhere` o puntos suspensivos para que nada desborde en pantallas angostas. La regla `[hidden] { display: none !important; }` garantiza que el atributo `hidden` gane sobre cualquier `display` de los componentes.

## Animaciones

Criterios:

- Cada animación tiene un propósito: señalar lo nuevo, indicar espera o marcar un estado transitorio.
- Solo se animan `opacity` y `transform` (baratas para el navegador). La única excepción es el resaltado de un pedido nuevo, que anima el color de fondo.
- Lo que entra usa `--curva-salida` (ease-out); los ciclos usan `--curva-suave` (ease-in-out).
- No hay animaciones de salida: los elementos que se van se quitan de inmediato.
- Las duraciones están en variables; no se escriben milisegundos sueltos en los componentes.

| Animación | Elementos | Duración | Curva | Propósito |
|---|---|---|---|---|
| `entrar` | `.msg`, `.nota`, `.bienvenida`, `.filtro-turno`, `.token`, JSON al abrir | `--dur-mensaje` (200 ms); 160 ms para el JSON | `--curva-salida` | Aparecer con un desplazamiento de 6 px |
| `entrar-chip` | `.datos > li` | `--dur-chip` (160 ms), retardo de 80 ms más 30 ms por chip, con tope en el octavo | `--curva-salida` | Que los metadatos lleguen después del texto, en secuencia |
| `punto` | Indicador de escritura | Ciclo de `--dur-puntos` (1200 ms), desfases de 15 % y 30 % | `--curva-suave` | Espera activa de la respuesta |
| `latido` | Punto de estado "iniciando" | `--dur-latido` (1600 ms) | `--curva-suave` | Estado transitorio del servidor |
| `resaltar` | `.item.nuevo` (pedido recién creado) | `--dur-resaltado` (1400 ms) | ease-out | Señalar qué cambió en el panel |
| Transiciones | Hover y foco de botones, campos y chips | `--dur-rapida` (120 ms) | ease-out | Respuesta inmediata al puntero |

El indicador de escritura aparece solo si la respuesta tarda más de 150 ms (`DEMORA_INDICADOR_MS` en `chat.js`), para que las respuestas rápidas no produzcan un parpadeo.

### Movimiento reducido

La sección "Preferencias del sistema" de la hoja de estilos contiene `@media (prefers-reduced-motion: reduce)`, que pone a cero las variables de duración y desplazamiento y aplica `animation: none` y `transition: none` a todo. Los puntos del indicador quedan estáticos y visibles. En JavaScript, `prefiereMenosMovimiento()` cambia el desplazamiento suave por uno instantáneo.

Para probarlo: herramientas de desarrollo → Rendering → "Emulate CSS media feature prefers-reduced-motion".

Al agregar una animación nueva: se define su duración como variable, se usan solo `opacity` y `transform`, se elige ease-out para entradas y se comprueba que el bloque de movimiento reducido la anula.

## Accesibilidad

- El hilo tiene `role="log"`, `aria-live="polite"` y `aria-relevant="additions"`. Cada respuesta se inserta completa (texto, chips y JSON) para que se anuncie una sola vez, con prefijos ocultos "Tú:" y "Asistente:".
- El indicador de escritura incluye el texto oculto "El asistente está escribiendo…".
- Todos los campos y el select tienen `<label>` (visible o con la clase `.solo-lectores`).
- Todos los botones tienen texto visible. Los chips de turno son `<button>` con `aria-pressed` y un `aria-label` que contiene el texto visible.
- El foco es visible en todo elemento interactivo (`:focus-visible`, contorno de 2 px en `--color-foco`). Las zonas con desplazamiento propio (hilo y lista de eventos) tienen `tabindex="0"` para poder recorrerlas con el teclado.
- Un enlace "Ir al campo de mensaje" aparece al tabular desde el inicio de la página.
- Los botones "Recargar" y "Borrar conversación" usan `aria-busy` en lugar de `disabled` mientras trabajan, para que el foco no se pierda.
- El log no es una región viva (sería ruidoso cada 3 s). La cabecera anuncia el estado del servicio con `role="status"` solo cuando cambia; `ponerTexto()` evita reasignar textos iguales.
- El log no se re-pinta mientras el foco está en uno de sus botones o hay texto seleccionado.
- El texto cumple contraste 4.5:1 o superior: para texto pequeño se usan las variantes `-texto` de cada color.
- Con punteros gruesos (pantallas táctiles), botones y campos miden al menos 44 px de alto.

## Seguridad del DOM

- Nunca se asigna `innerHTML` con datos. El DOM se construye con `crear()` y `textContent`.
- La única asignación de `innerHTML` está en `ponerHtmlFormateado()`, que usa `formatear()`: primero escapa todo el texto y después inserta `<strong>` y `<br>`. Las únicas etiquetas del resultado son esas dos.
- El JSON de la respuesta completa y los campos del log se muestran como texto.
- El token de administración queda en `localStorage`, accesible para cualquier script del origen; la CSP limita qué scripts pueden existir. El botón "Olvidar token de administración" lo elimina, y un token rechazado se borra automáticamente.

## Almacenamiento local

Todas las lecturas y escrituras pasan por `leerLocal()` y `guardarLocal()`, que nunca lanzan errores (modo privado, almacenamiento bloqueado o cuota llena). Las claves están en `util.CLAVES`. Un navegador que usó una versión anterior conserva claves `techstore.*` que ya no se leen: el cliente vuelve al ID por defecto y el token de administración se pide de nuevo.

| Clave | Contenido | Escribe |
|---|---|---|
| `libreria.usuario` | ID del cliente (también es el `session_id`) | `panel.js` |
| `libreria.tokenAdmin` | Token para `X-Admin-Token` | `api.js` (desde `logs.js`) |
| `libreria.nivelLogs` | Último nivel elegido en el filtro del log | `logs.js` |

## Cómo agregar un componente nuevo

El ejemplo agrega un bloque "Documentos del corpus" al panel, a partir de un endpoint hipotético `GET /api/documentos`.

1. Marcado en `index.html`, sin estilos ni manejadores en línea:

   ```html
   <section class="bloque" aria-labelledby="titulo-documentos">
     <h2 class="bloque-titulo" id="titulo-documentos">Documentos</h2>
     <div id="documentos"><p class="vacio">Cargando documentos…</p></div>
   </section>
   ```

2. Función del endpoint en `api.js`, siempre a través de `peticion()` (con `admin: true` si es de administración):

   ```js
   export function listarDocumentos(opciones = {}) {
     return peticion('/api/documentos', opciones);
   }
   ```

3. Módulo `js/documentos.js` que obtiene los nodos con `porId`, construye el DOM con `crear` y devuelve una API pequeña:

   ```js
   import { listarDocumentos } from './api.js';
   import { crear, porId } from './util.js';

   export function iniciarDocumentos() {
     const caja = porId('documentos');

     async function cargar() {
       try {
         const d = await listarDocumentos();
         const items = (d?.documentos ?? []).map((doc) =>
           crear('li', { clase: 'item', texto: doc.nombre }));
         caja.replaceChildren(crear('ul', { clase: 'lista' }, items));
       } catch (error) {
         caja.replaceChildren(crear('p', { clase: 'vacio error', texto: error.mensaje }));
       }
     }

     return { cargar };
   }
   ```

4. Conexión en `main.js`: se crea junto a los demás y se llama donde corresponda (arranque, `alQuedarListo`, temporizadores). Los temporizadores viven en `main.js`; los módulos solo exponen funciones.

5. Estilos en la hoja que corresponda (`chat.css`, `paneles.css`…), con variables de `base.css`. Los ajustes por ancho van en `responsive.css`.

6. Comprobaciones: etiquetas y nombres accesibles, foco visible, comportamiento con movimiento reducido, ningún `innerHTML` con datos y ningún atributo `style` u `on*`.

## Verificación

Sintaxis de cada módulo, sin servidor:

```bash
for f in frontend/js/*.js; do node --input-type=module --check < "$f" && echo "ok $f"; done
```

Lista de revisión manual con el backend en marcha:

- Consola sin errores ni mensajes `Refused to …` de la CSP.
- Anchos de 1440, 1100 y 375 px sin desplazamiento horizontal.
- Navegación completa con teclado: Tab llega a todos los controles, Enter envía, Shift+Enter agrega una línea.
- Movimiento reducido emulado: nada se anima.
- Mensajes enviados en ráfaga: aparece el aviso de límite con la cuenta regresiva.
- Una recomendación muestra tarjetas con precio en la moneda de la tienda, stock, valoración y edad; un libro agotado dice "agotado".
- La cabecera y el título de la pestaña muestran el nombre de `datos/tienda.json`.
- Backend detenido: la cabecera pasa a "Sin conexión" y vuelve sola a "En línea" al reiniciarlo.
- Con token de administración configurado: el panel de logs pide el token y, tras guardarlo, muestra los eventos.
