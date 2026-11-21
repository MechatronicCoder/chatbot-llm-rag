/**
 * api.js — cliente HTTP del backend.
 *
 * Todas las rutas son absolutas y del mismo origen. Cada función devuelve el
 * JSON de la respuesta o lanza un ErrorApi con un mensaje listo para mostrar.
 * Los endpoints de administración (logs y métricas) envían X-Admin-Token
 * cuando hay un token guardado.
 */

import { CLAVES, leerLocal, guardarLocal, borrarLocal } from './util.js';

const TIEMPO_LIMITE_MS = 15000;
const TIEMPO_LIMITE_CHAT_MS = 120000; // un turno con modelo y herramientas puede tardar

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

/**
 * Error de cualquier llamada a la API.
 *
 * - status: código HTTP (0 si no hubo respuesta: red caída, tiempo agotado o cancelación).
 * - mensaje: texto amable, en español, apto para mostrar tal cual.
 * - reintentarEn: segundos indicados por Retry-After (solo en 429), o null.
 * - detalle: texto del campo `detail` del backend, o null.
 * - tipo: 'http' | 'red' | 'tiempo' | 'cancelada' | 'formato'.
 */
export class ErrorApi extends Error {
  constructor(status, mensaje, { reintentarEn = null, detalle = null, tipo = 'http' } = {}) {
    super(mensaje);
    this.name = 'ErrorApi';
    this.status = status;
    this.mensaje = mensaje;
    this.reintentarEn = reintentarEn;
    this.detalle = detalle;
    this.tipo = tipo;
  }

  get esDeRed() {
    return this.tipo === 'red' || this.tipo === 'tiempo';
  }

  get sinPermiso() {
    return this.status === 401 || this.status === 403;
  }
}

/** Retry-After admite segundos ("12") o una fecha HTTP. Devuelve segundos o null. */
export function segundosDeRetryAfter(valor, ahora = Date.now()) {
  if (valor === null || valor === undefined) return null;
  const texto = String(valor).trim();
  if (!texto) return null;
  if (/^\d+$/.test(texto)) return Math.max(1, Number(texto));
  const fecha = Date.parse(texto);
  if (Number.isNaN(fecha)) return null;
  return Math.max(1, Math.ceil((fecha - ahora) / 1000));
}

/**
 * FastAPI devuelve `detail` como texto (HTTPException) o como lista de
 * errores de validación [{loc, msg, type}]. Ambos casos se convierten en texto.
 */
function textoDelDetalle(cuerpo) {
  const detalle = cuerpo && typeof cuerpo === 'object' ? cuerpo.detail : cuerpo;
  if (typeof detalle === 'string') return detalle.trim();
  if (Array.isArray(detalle)) {
    return detalle
      .map((e) => {
        const campo = Array.isArray(e?.loc) ? e.loc.filter((p) => p !== 'body').join('.') : '';
        const msg = e?.msg ?? String(e);
        return campo ? `${campo}: ${msg}` : msg;
      })
      .join(' · ');
  }
  if (detalle && typeof detalle === 'object') return JSON.stringify(detalle);
  return '';
}

function errorDesdeRespuesta(respuesta, cuerpo) {
  const { status } = respuesta;
  const detalle = textoDelDetalle(cuerpo) || null;

  if (status === 429) {
    const s = segundosDeRetryAfter(respuesta.headers.get('Retry-After'));
    const mensaje = s !== null
      ? `Se alcanzó el límite de mensajes por minuto, intenta en ${s} s.`
      : 'Se alcanzó el límite de mensajes por minuto, intenta de nuevo en unos segundos.';
    return new ErrorApi(status, mensaje, { reintentarEn: s, detalle });
  }
  if (status === 422) {
    return new ErrorApi(status, detalle || 'Los datos enviados no son válidos.', { detalle });
  }
  if (status === 401 || status === 403) {
    return new ErrorApi(status, 'Se requiere un token de administración válido.', { detalle });
  }
  if (status === 404) {
    return new ErrorApi(status, detalle || 'No se encontró el recurso solicitado.', { detalle });
  }
  if (status === 503) {
    return new ErrorApi(status, 'El servidor todavía está iniciando. Intenta de nuevo en unos segundos.', { detalle });
  }
  if (status >= 500) {
    return new ErrorApi(status, `El servidor tuvo un problema (${status}). Intenta de nuevo en unos segundos.`, { detalle });
  }
  return new ErrorApi(status, detalle || `La solicitud falló (${status}).`, { detalle });
}

// ---------------------------------------------------------------------------
// Token de administración
// ---------------------------------------------------------------------------

export function obtenerTokenAdmin() {
  return leerLocal(CLAVES.tokenAdmin, '') || '';
}

export function guardarTokenAdmin(token) {
  return guardarLocal(CLAVES.tokenAdmin, String(token ?? '').trim());
}

export function borrarTokenAdmin() {
  return borrarLocal(CLAVES.tokenAdmin);
}

// ---------------------------------------------------------------------------
// Petición base
// ---------------------------------------------------------------------------

async function leerCuerpo(respuesta) {
  if (respuesta.status === 204) return null;
  const texto = await respuesta.text();
  if (!texto) return null;
  try {
    return JSON.parse(texto);
  } catch {
    return texto;
  }
}

/**
 * @param {string} ruta     ruta absoluta del mismo origen, p. ej. '/api/health'
 * @param {object} opciones { metodo, cuerpo, admin, tiempoLimite, senal }
 */
async function peticion(ruta, { metodo = 'GET', cuerpo, admin = false, tiempoLimite = TIEMPO_LIMITE_MS, senal } = {}) {
  const cabeceras = { Accept: 'application/json' };
  if (cuerpo !== undefined) cabeceras['Content-Type'] = 'application/json';
  if (admin) {
    const token = obtenerTokenAdmin();
    if (token) cabeceras['X-Admin-Token'] = token;
  }

  const control = new AbortController();
  let vencido = false;
  const temporizador = setTimeout(() => {
    vencido = true;
    control.abort();
  }, tiempoLimite);
  const alCancelar = () => control.abort();
  if (senal) {
    if (senal.aborted) control.abort();
    else senal.addEventListener('abort', alCancelar, { once: true });
  }

  let respuesta;
  let datos;
  try {
    respuesta = await fetch(ruta, {
      method: metodo,
      headers: cabeceras,
      body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
      signal: control.signal,
      cache: 'no-store',
      credentials: 'same-origin',
    });
    datos = await leerCuerpo(respuesta);
  } catch (error) {
    if (vencido) {
      throw new ErrorApi(0, 'El servidor tardó demasiado en responder. Intenta de nuevo.', { tipo: 'tiempo' });
    }
    if (senal?.aborted) {
      throw new ErrorApi(0, 'Solicitud cancelada.', { tipo: 'cancelada' });
    }
    throw new ErrorApi(0, 'No se pudo conectar con el servidor. Revisa tu conexión o que la API esté en marcha.', { tipo: 'red', detalle: error?.message ?? null });
  } finally {
    clearTimeout(temporizador);
    senal?.removeEventListener('abort', alCancelar);
  }

  if (!respuesta.ok) throw errorDesdeRespuesta(respuesta, datos);

  // Un proxy o una ruta inexistente servida como HTML no debe pasar por JSON válido.
  if (typeof datos === 'string') {
    throw new ErrorApi(respuesta.status, 'La respuesta del servidor no tiene el formato esperado.', { tipo: 'formato' });
  }
  return datos;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

/** GET /api/health */
export function salud(opciones = {}) {
  return peticion('/api/health', opciones);
}

/** GET /api/productos?consulta=&categoria=&limite= */
export function buscarProductos({ consulta = '', categoria = '', limite = 8 } = {}, opciones = {}) {
  const parametros = new URLSearchParams();
  if (consulta.trim()) parametros.set('consulta', consulta.trim());
  if (categoria) parametros.set('categoria', categoria);
  parametros.set('limite', String(limite));
  return peticion(`/api/productos?${parametros}`, opciones);
}

/** GET /api/pedidos/{usuario_id} */
export function pedidosDe(usuarioId, opciones = {}) {
  return peticion(`/api/pedidos/${encodeURIComponent(usuarioId)}`, opciones);
}

/** POST /api/chat */
export function enviarMensaje({ mensaje, usuarioId, sessionId = usuarioId }, opciones = {}) {
  return peticion('/api/chat', {
    tiempoLimite: TIEMPO_LIMITE_CHAT_MS,
    ...opciones,
    metodo: 'POST',
    cuerpo: { mensaje, usuario_id: usuarioId, session_id: sessionId },
  });
}

/** DELETE /api/sesiones/{session_id} */
export function borrarSesion(sessionId, opciones = {}) {
  return peticion(`/api/sesiones/${encodeURIComponent(sessionId)}`, { ...opciones, metodo: 'DELETE' });
}

/** GET /api/logs?n=&nivel=&turno= (administración) */
export function leerLogs({ n = 60, nivel = '', turno = '' } = {}, opciones = {}) {
  const parametros = new URLSearchParams({ n: String(n) });
  if (nivel) parametros.set('nivel', nivel);
  if (turno) parametros.set('turno', turno);
  return peticion(`/api/logs?${parametros}`, { ...opciones, admin: true });
}

/** GET /api/metricas (administración) */
export function leerMetricas(opciones = {}) {
  return peticion('/api/metricas', { ...opciones, admin: true });
}
