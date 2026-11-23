/**
 * acceso.js — pantalla de acceso de la demo (usuario y clave).
 *
 * Si el backend tiene el acceso activado, cubre la página hasta que se inicia
 * sesión, y vuelve a aparecer si la sesión vence. La sesión es una cookie
 * HttpOnly que este código nunca lee: solo consulta /api/acceso/estado. La clave
 * no se guarda en ningún lado y el campo se vacía después de cada intento.
 */

import { cerrarSesion, estadoAcceso, iniciarSesion } from './api.js';
import { porId, ponerTexto } from './util.js';

function enMinutos(segundos) {
  if (!Number.isFinite(segundos)) return 'unos minutos';
  const minutos = Math.max(1, Math.ceil(segundos / 60));
  return minutos === 1 ? '1 minuto' : `${minutos} minutos`;
}

/**
 * @param {object} opciones
 * @param {() => void} [opciones.alEntrar]  después de iniciar sesión (recargar datos)
 * @param {() => void} [opciones.alSalir]   después de cerrar sesión (limpiar la conversación)
 */
export function iniciarAcceso({ alEntrar, alSalir } = {}) {
  const capa = porId('acceso');
  const formulario = porId('acceso-formulario');
  const usuario = porId('acceso-usuario');
  const clave = porId('acceso-clave');
  const boton = porId('acceso-entrar');
  const error = porId('acceso-error');
  const salir = porId('salir');
  // Mientras la pantalla está visible, el resto de la página no recibe foco ni clics.
  const fondo = [document.querySelector('.cabecera'), document.querySelector('main')].filter(Boolean);
  let dentro = false; // hubo una sesión abierta en esta pestaña

  function mostrarError(texto) {
    ponerTexto(error, texto);
    error.hidden = !texto;
  }

  function mostrar(mensaje = '') {
    const yaVisible = !capa.hidden;
    dentro = false;
    capa.hidden = false;
    fondo.forEach((nodo) => { nodo.inert = true; });
    salir.hidden = true;
    // Con la pantalla ya visible no se pisa un error que la persona está leyendo.
    if (!yaVisible || mensaje) mostrarError(mensaje);
    if (!yaVisible) usuario.focus();
  }

  /** Lo llama api.js ante un 401 de acceso: sesión que venció o que nunca existió. */
  function sesionRechazada() {
    mostrar(dentro ? 'Tu sesión terminó. Ingresa de nuevo para continuar.' : '');
  }

  function ocultar(requerido) {
    dentro = requerido;
    capa.hidden = true;
    fondo.forEach((nodo) => { nodo.inert = false; });
    salir.hidden = !requerido;
    clave.value = '';
    mostrarError('');
  }

  formulario.addEventListener('submit', async (evento) => {
    evento.preventDefault();
    if (boton.disabled) return;
    boton.disabled = true;
    mostrarError('');
    try {
      const d = await iniciarSesion({ usuario: usuario.value.trim(), clave: clave.value });
      ocultar(Boolean(d?.requerido));
      alEntrar?.();
    } catch (e) {
      clave.value = '';
      if (e?.status === 429) mostrarError(`Demasiados intentos. Espera ${enMinutos(e.reintentarEn)} y vuelve a intentar.`);
      else if (e?.status === 401) mostrarError('Usuario o clave incorrectos.');
      else if (e?.status === 422) mostrarError('Escribe el usuario y la clave.');
      else mostrarError(e?.mensaje ?? 'No se pudo iniciar sesión.');
      clave.focus();
    } finally {
      boton.disabled = false;
    }
  });

  salir.addEventListener('click', async () => {
    try {
      await cerrarSesion();
    } catch {
      // Aunque falle la llamada, se muestra la pantalla: la cookie vence sola.
    }
    alSalir?.();
    mostrar();
  });

  /** Consulta el estado al arrancar; sin conexión no muestra nada (lo informa la cabecera). */
  async function verificar() {
    try {
      const d = await estadoAcceso();
      if (d?.requerido && !d.autenticado) mostrar();
      else ocultar(Boolean(d?.requerido));
      return d;
    } catch {
      return null;
    }
  }

  /** La pantalla de acceso está visible: no hay que pedir datos protegidos. */
  function bloqueado() {
    return !capa.hidden;
  }

  return { verificar, mostrar, sesionRechazada, bloqueado };
}
