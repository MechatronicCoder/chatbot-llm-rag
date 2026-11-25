/**
 * modo.js — interruptor entre la vista de cliente y la de programador.
 *
 * - Cliente (💬): solo la conversación, sin datos técnicos.
 * - Programador (</>): panel del cliente, log del turno, métricas, y en cada respuesta la
 *   ruta, el modelo, los tokens y el JSON completo.
 * - Pasar a programador pide el token de administración cuando el servidor lo exige, y lo
 *   valida contra /api/metricas antes de cambiar: sin un token válido, la vista no cambia.
 * - La vista se recuerda en este navegador; al recargar, la de programador se vuelve a validar.
 *
 * Ocultar es solo presentación: los logs y las métricas los protege el servidor.
 */

import { borrarTokenAdmin, guardarTokenAdmin, leerMetricas } from './api.js';
import { guardarLocal, leerLocal, porId, ponerTexto } from './util.js';

const CLAVE = 'vista';

export function iniciarModo({ alCambiar = () => {} } = {}) {
  const botones = { cliente: porId('vista-cliente'), programador: porId('vista-programador') };
  const dialogo = porId('dialogo-programador');
  const formulario = porId('programador-formulario');
  const entrada = porId('programador-token');
  const error = porId('programador-error');
  const entrar = porId('programador-entrar');
  let actual = 'cliente';
  let focoPrevio = null;

  function poner(modo) {
    actual = modo;
    document.body.dataset.modo = modo;
    for (const [nombre, boton] of Object.entries(botones)) {
      boton.setAttribute('aria-pressed', String(nombre === modo));
    }
    guardarLocal(CLAVE, modo);
    window.dispatchEvent(new Event('resize')); // los divisores recalculan con las columnas a la vista
    alCambiar(modo);
  }

  /** true si el servidor acepta el token guardado (o no exige ninguno). */
  async function tokenValido() {
    try {
      await leerMetricas();
      return true;
    } catch (e) {
      if (e?.sinPermiso) return false;
      throw e;
    }
  }

  function mostrarError(texto) {
    ponerTexto(error, texto);
    error.hidden = !texto;
  }

  function abrirDialogo() {
    focoPrevio = document.activeElement;
    entrada.value = '';
    mostrarError('');
    dialogo.hidden = false;
    entrada.focus();
  }

  function cerrarDialogo() {
    dialogo.hidden = true;
    focoPrevio?.focus?.();
  }

  async function pedirProgramador() {
    if (actual === 'programador') return;
    try {
      if (await tokenValido()) {
        poner('programador');
        return;
      }
    } catch {
      // sin respuesta del servidor: se pide el token y se valida al enviarlo
    }
    abrirDialogo();
  }

  formulario.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const valor = entrada.value.trim();
    if (!valor) {
      mostrarError('Ingresa el token de administración.');
      entrada.focus();
      return;
    }
    entrar.disabled = true;
    guardarTokenAdmin(valor);
    try {
      if (await tokenValido()) {
        cerrarDialogo();
        poner('programador');
      } else {
        borrarTokenAdmin();
        mostrarError('El token no es válido.');
        entrada.select();
      }
    } catch (e) {
      borrarTokenAdmin();
      mostrarError(e?.mensaje ?? 'No se pudo validar el token.');
    } finally {
      entrar.disabled = false;
    }
  });

  porId('programador-cancelar').addEventListener('click', cerrarDialogo);
  dialogo.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') cerrarDialogo();
  });
  botones.cliente.addEventListener('click', () => poner('cliente'));
  botones.programador.addEventListener('click', pedirProgramador);

  document.body.dataset.modo = 'cliente';
  if (leerLocal(CLAVE) === 'programador') {
    tokenValido()
      .then((ok) => ok && poner('programador'))
      .catch(() => {});
  }

  return { esProgramador: () => actual === 'programador' };
}
