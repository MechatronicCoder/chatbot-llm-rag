/**
 * divisores.js — bordes arrastrables entre las tres columnas (conversación, panel y log).
 *
 * - Cada borde reparte el ancho entre las dos columnas que separa; la tercera no cambia.
 * - Ninguna columna queda más angosta que 1/6 del ancho de la ventana, tampoco al
 *   achicar la ventana.
 * - Con el teclado: flechas (Shift para pasos largos). Doble clic o Inicio vuelven al
 *   ancho original.
 * - Los anchos se recuerdan en este navegador. Solo aplica con las tres columnas a la
 *   vista (1280 px o más); en pantallas menores los bordes se ocultan.
 */

import { borrarLocal, guardarLocal, leerLocal, porId } from './util.js';

const CLAVE = 'anchos-columnas';
const FRACCION_MINIMA = 1 / 6;
const PASO = 16;
const PASO_LARGO = 64;
const TRES_COLUMNAS = window.matchMedia('(min-width: 1280px)');

const limitar = (valor, minimo, maximo) => Math.min(Math.max(valor, minimo), maximo);

function leerAnchos() {
  try {
    const guardado = JSON.parse(leerLocal(CLAVE, 'null'));
    if (Number.isFinite(guardado?.panel) && Number.isFinite(guardado?.logs)) return guardado;
  } catch {
    // un valor dañado se ignora: se vuelve al ancho original
  }
  return null;
}

export function iniciarDivisores() {
  const disposicion = document.querySelector('.disposicion');
  const columnas = {
    chat: document.querySelector('.chat'),
    panel: document.querySelector('.panel'),
    logs: document.querySelector('.logs'),
  };
  const divisores = [
    { nodo: porId('divisor-panel'), izquierda: 'chat', derecha: 'panel' },
    { nodo: porId('divisor-logs'), izquierda: 'panel', derecha: 'logs' },
  ];
  const raiz = getComputedStyle(document.documentElement);
  const originales = {
    panel: parseFloat(raiz.getPropertyValue('--ancho-panel')) || 320,
    logs: parseFloat(raiz.getPropertyValue('--ancho-logs')) || 380,
  };
  let preferidos = leerAnchos(); // lo que eligió la persona; null = ancho original

  const minimo = () => document.documentElement.clientWidth * FRACCION_MINIMA;
  const ancho = (nombre) => columnas[nombre].getBoundingClientRect().width;

  /** Los anchos más cercanos a los pedidos que dejan a las tres columnas sobre el mínimo. */
  function acotar({ panel, logs }) {
    const total = disposicion.clientWidth;
    const min = minimo();
    const l = limitar(logs, min, total - 2 * min);
    return { panel: limitar(panel, min, total - l - min), logs: l };
  }

  function aplicar(anchos) {
    disposicion.style.setProperty('--ancho-panel', `${Math.round(anchos.panel)}px`);
    disposicion.style.setProperty('--ancho-logs', `${Math.round(anchos.logs)}px`);
    for (const { nodo, izquierda, derecha } of divisores) {
      const izq = ancho(izquierda);
      const max = izq + ancho(derecha) - minimo();
      nodo.setAttribute('aria-valuemin', String(Math.round(minimo())));
      nodo.setAttribute('aria-valuemax', String(Math.round(max)));
      nodo.setAttribute('aria-valuenow', String(Math.round(izq)));
    }
  }

  function ajustarAVentana() {
    if (TRES_COLUMNAS.matches) aplicar(acotar(preferidos ?? originales));
  }

  /** Anchos tras mover el borde `i` desde `inicio`; `delta` > 0 lo mueve a la derecha. */
  function mover(i, inicio, delta) {
    const min = minimo();
    const { panel, logs } = inicio;
    if (i === 0) {
      const chat = disposicion.clientWidth - panel - logs;
      return { panel: panel - limitar(delta, min - chat, panel - min), logs };
    }
    const d = limitar(delta, min - panel, logs - min);
    return { panel: panel + d, logs: logs - d };
  }

  function elegir(anchos) {
    preferidos = anchos;
    aplicar(anchos);
    guardarLocal(CLAVE, JSON.stringify({ panel: Math.round(anchos.panel), logs: Math.round(anchos.logs) }));
  }

  function restablecer() {
    preferidos = null;
    borrarLocal(CLAVE);
    ajustarAVentana();
  }

  divisores.forEach(({ nodo }, i) => {
    let arrastre = null; // { x, inicio } mientras se arrastra

    nodo.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      nodo.setPointerCapture(e.pointerId);
      arrastre = { x: e.clientX, inicio: { panel: ancho('panel'), logs: ancho('logs') } };
      nodo.dataset.arrastrando = '';
      document.body.classList.add('redimensionando');
    });

    nodo.addEventListener('pointermove', (e) => {
      if (arrastre) aplicar(mover(i, arrastre.inicio, e.clientX - arrastre.x));
    });

    const soltar = () => {
      if (!arrastre) return;
      arrastre = null;
      delete nodo.dataset.arrastrando;
      document.body.classList.remove('redimensionando');
      elegir({ panel: ancho('panel'), logs: ancho('logs') });
    };
    nodo.addEventListener('pointerup', soltar);
    nodo.addEventListener('pointercancel', soltar);
    nodo.addEventListener('lostpointercapture', soltar);

    nodo.addEventListener('dblclick', restablecer);

    nodo.addEventListener('keydown', (e) => {
      if (e.key === 'Home') {
        e.preventDefault();
        restablecer();
        return;
      }
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      const paso = (e.shiftKey ? PASO_LARGO : PASO) * (e.key === 'ArrowLeft' ? -1 : 1);
      elegir(mover(i, { panel: ancho('panel'), logs: ancho('logs') }, paso));
    });
  });

  window.addEventListener('resize', ajustarAVentana);
  TRES_COLUMNAS.addEventListener('change', ajustarAVentana);
  ajustarAVentana();
}
