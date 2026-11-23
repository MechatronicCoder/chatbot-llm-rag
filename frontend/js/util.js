/**
 * util.js — utilidades sin dependencias.
 *
 * - Escape de HTML y formato mínimo (**negrita** y saltos de línea).
 * - Lectura y escritura de localStorage que nunca lanza excepciones.
 * - Ayudas de DOM para construir nodos sin innerHTML.
 * - Formateo de precios, horas, duraciones y puntajes.
 */

// ---------------------------------------------------------------------------
// Claves de localStorage (todas en un solo lugar)
// ---------------------------------------------------------------------------

export const CLAVES = Object.freeze({
  usuario: 'libreria.usuario',
  tokenAdmin: 'libreria.tokenAdmin',
  nivelLogs: 'libreria.nivelLogs',
});

// ---------------------------------------------------------------------------
// Escape y formato
// ---------------------------------------------------------------------------

const ESCAPES = Object.freeze({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
});

/** Escapa los caracteres con significado en HTML. Acepta cualquier valor. */
export function escaparHtml(valor) {
  return String(valor ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/**
 * Convierte texto plano en HTML seguro con un formato mínimo:
 * `**negrita**` pasa a <strong> y cada salto de línea pasa a <br>.
 * El escape ocurre SIEMPRE antes de aplicar el formato, de modo que las
 * únicas etiquetas del resultado son las que inserta esta función.
 */
export function formatear(texto) {
  return escaparHtml(texto)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\r\n|\r|\n/g, '<br>');
}

/** Único punto del frontend que asigna innerHTML, y solo con `formatear`. */
export function ponerHtmlFormateado(nodo, texto) {
  nodo.innerHTML = formatear(texto);
}

/** Recorta un texto largo y agrega puntos suspensivos. */
export function resumir(texto, maximo = 160) {
  const limpio = String(texto ?? '').replace(/\s+/g, ' ').trim();
  return limpio.length > maximo ? `${limpio.slice(0, maximo - 1)}…` : limpio;
}

// ---------------------------------------------------------------------------
// localStorage tolerante a fallos
// ---------------------------------------------------------------------------
// En modo privado, con cookies bloqueadas o con la cuota llena, el acceso a
// localStorage puede lanzar. Ninguna de estas funciones propaga el error.

function almacen() {
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}

/** Devuelve el valor guardado o `porDefecto` si no existe o si falla. */
export function leerLocal(clave, porDefecto = null) {
  try {
    const valor = almacen()?.getItem(clave);
    return valor ?? porDefecto;
  } catch {
    return porDefecto;
  }
}

/** Guarda un valor; `null`, `undefined` o '' lo eliminan. Devuelve true si funcionó. */
export function guardarLocal(clave, valor) {
  try {
    const a = almacen();
    if (!a) return false;
    if (valor === null || valor === undefined || valor === '') a.removeItem(clave);
    else a.setItem(clave, String(valor));
    return true;
  } catch {
    return false;
  }
}

export function borrarLocal(clave) {
  return guardarLocal(clave, null);
}

// ---------------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------------

/** getElementById que falla con un mensaje claro si index.html no tiene el nodo. */
export function porId(id) {
  const nodo = document.getElementById(id);
  if (!nodo) throw new Error(`Falta el elemento #${id} en index.html`);
  return nodo;
}

/**
 * Crea un elemento sin usar innerHTML.
 *
 *   crear('li', { clase: ['dato', activo && 'activo'], texto: 'hola',
 *                 atributos: { title: 'x' }, datos: { turno: 't-1' } }, [hijos])
 *
 * - `texto` se asigna con textContent (nunca se interpreta como HTML).
 * - Los atributos con valor null, undefined o false se omiten; true crea un
 *   atributo vacío.
 * - `style` y los manejadores `on*` están prohibidos: la Content-Security-Policy
 *   los bloquearía de todos modos. Los estilos van en las hojas de css/ y los eventos
 *   con addEventListener.
 * - Los hijos pueden ser nodos o cadenas (las cadenas se insertan como texto).
 */
export function crear(etiqueta, opciones = {}, hijos = []) {
  const { clase, texto, atributos, datos } = opciones;
  const nodo = document.createElement(etiqueta);

  if (clase) {
    nodo.className = Array.isArray(clase) ? clase.filter(Boolean).join(' ') : clase;
  }
  if (texto !== undefined && texto !== null) nodo.textContent = String(texto);

  if (atributos) {
    for (const [nombre, valor] of Object.entries(atributos)) {
      if (nombre === 'style' || /^on/i.test(nombre)) {
        throw new Error(`crear(): el atributo "${nombre}" no está permitido`);
      }
      if (valor === null || valor === undefined || valor === false) continue;
      nodo.setAttribute(nombre, valor === true ? '' : String(valor));
    }
  }
  if (datos) {
    for (const [nombre, valor] of Object.entries(datos)) {
      if (valor !== null && valor !== undefined) nodo.dataset[nombre] = String(valor);
    }
  }

  for (const hijo of [].concat(hijos)) {
    if (hijo !== null && hijo !== undefined && hijo !== false) nodo.append(hijo);
  }
  return nodo;
}

/** Quita todos los hijos de un nodo. */
export function vaciar(nodo) {
  nodo.replaceChildren();
}

/** Asigna textContent solo si cambia (evita anuncios repetidos en regiones vivas). */
export function ponerTexto(nodo, texto) {
  const valor = String(texto ?? '');
  if (nodo.textContent !== valor) nodo.textContent = valor;
}

export function prefiereMenosMovimiento() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/** true si el contenedor está desplazado (casi) hasta el final. */
export function cercaDelFinal(nodo, margen = 48) {
  return nodo.scrollHeight - nodo.scrollTop - nodo.clientHeight <= margen;
}

/** Desplaza un contenedor hasta una posición; suave salvo reduced-motion. */
export function desplazarA(nodo, top, suave = false) {
  const behavior = suave && !prefiereMenosMovimiento() ? 'smooth' : 'auto';
  nodo.scrollTo({ top, behavior });
}

export function irAlFinal(nodo, suave = false) {
  desplazarA(nodo, nodo.scrollHeight, suave);
}

/** true si hay texto seleccionado dentro del nodo (para no re-pintar mientras se copia). */
export function haySeleccionDentro(nodo) {
  const seleccion = window.getSelection?.();
  if (!seleccion || seleccion.isCollapsed || seleccion.rangeCount === 0) return false;
  return nodo.contains(seleccion.getRangeAt(0).commonAncestorContainer);
}

// ---------------------------------------------------------------------------
// Formato de datos
// ---------------------------------------------------------------------------

const FORMATO_DINERO = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

let simboloMoneda = 'S/';

/** Símbolo de la moneda de la tienda (llega en /api/health). */
export function configurarMoneda(simbolo) {
  if (typeof simbolo === 'string' && simbolo.trim()) simboloMoneda = simbolo.trim();
}

/** 1299.5 → "S/ 1,299.50". Valores no numéricos → "—". */
export function formatearPrecio(valor) {
  const n = Number(valor);
  return valor !== null && valor !== '' && Number.isFinite(n)
    ? `${simboloMoneda} ${FORMATO_DINERO.format(n)}`
    : '—';
}

/** Número entero legible; null/undefined → "—". */
export function formatearCantidad(valor) {
  const n = Number(valor);
  return valor !== null && valor !== undefined && Number.isFinite(n) ? String(Math.round(n)) : '—';
}

/** Milisegundos redondeados con unidad. */
export function formatearMs(valor) {
  const n = Number(valor);
  return valor !== null && valor !== undefined && Number.isFinite(n) ? `${Math.round(n)} ms` : '—';
}

/** Puntaje de similitud con dos decimales. */
export function formatearPuntaje(valor) {
  const n = Number(valor);
  return valor !== null && valor !== undefined && Number.isFinite(n) ? n.toFixed(2) : '—';
}

/** "2026-11-21T10:04:05.123" → "10:04:05.123". */
export function formatearHora(ts) {
  const texto = String(ts ?? '');
  return texto.length >= 19 ? texto.slice(11, 23) : texto;
}

/** Segundos → "45 s", "12 min", "3 h 5 min", "2 d 4 h". */
export function formatearDuracion(segundos) {
  const s = Math.max(0, Math.floor(Number(segundos) || 0));
  if (s < 60) return `${s} s`;
  const min = Math.floor(s / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ${min % 60} min`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

/** Plural simple: plural(3, 'mensaje') → "3 mensajes". */
export function plural(n, singular, pluralForma = `${singular}s`) {
  return `${n} ${Number(n) === 1 ? singular : pluralForma}`;
}
