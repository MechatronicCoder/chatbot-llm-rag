/**
 * chat.js — hilo de mensajes y formulario de envío.
 *
 * Pinta las burbujas del cliente y del asistente, las tarjetas de los libros
 * mostrados (libros.js) y del pedido (carrito.js), los chips de metadatos de cada respuesta, el bloque
 * "ver respuesta completa" con el JSON, el indicador de escritura y los
 * errores. No conoce el panel ni los logs: se comunica con ellos mediante las
 * funciones que recibe en iniciarChat().
 */

import { crearAgrupador } from './agrupador.js';
import { enviarMensaje, ErrorApi } from './api.js';
import { tarjetaCarrito } from './carrito.js';
import { chipsDe, detalleJson } from './detalles.js';
import { tarjetasDeLibros } from './libros.js';
import {
  crear,
  porId,
  vaciar,
  ponerHtmlFormateado,
  irAlFinal,
  desplazarA,
} from './util.js';

const SUGERENCIAS = [
  'Algo de terror para mi sobrino de 13 años',
  'Un misterio como los de Agatha Christie',
  'Fantasía épica para empezar una saga',
  '¿Cuánto cuesta el envío?',
];

/** Si la respuesta llega antes de este tiempo no se muestra el indicador (evita un parpadeo). */
const DEMORA_INDICADOR_MS = 150;

/**
 * @param {object} opciones
 * @param {() => string} opciones.obtenerUsuario  id del cliente (también es el session_id)
 * @param {(turnoId: string) => void} [opciones.alFiltrarTurno]  '' quita el filtro
 * @param {(respuesta: object) => void} [opciones.alResponder]
 */
export function iniciarChat({ obtenerUsuario, alFiltrarTurno, alResponder } = {}) {
  const hilo = porId('hilo');
  const formulario = porId('formulario');
  const entrada = porId('entrada');
  const boton = porId('enviar');
  const textoBoton = boton.textContent.trim() || 'Enviar';

  let enviando = false;
  let bloqueadoHasta = 0;
  let temporizadorBloqueo = null;
  let turnoFiltrado = '';

  // -------------------------------------------------------------------------
  // Estado del botón (espera de respuesta y límite de mensajes)
  // -------------------------------------------------------------------------

  function segundosBloqueo() {
    return Math.max(0, Math.ceil((bloqueadoHasta - Date.now()) / 1000));
  }

  // Mientras el asistente responde se puede seguir escribiendo: lo nuevo espera en la cola.
  function actualizarBoton() {
    const restante = segundosBloqueo();
    boton.disabled = restante > 0;
    const texto = restante > 0 ? `Espera ${restante} s` : textoBoton;
    if (boton.textContent !== texto) boton.textContent = texto;
  }

  function bloquearDurante(segundos) {
    bloqueadoHasta = Date.now() + segundos * 1000;
    clearInterval(temporizadorBloqueo);
    temporizadorBloqueo = setInterval(() => {
      actualizarBoton();
      if (segundosBloqueo() === 0) {
        clearInterval(temporizadorBloqueo);
        temporizadorBloqueo = null;
        procesarCola(); // lo que se escribió durante la espera sale ahora
      }
    }, 250);
    actualizarBoton();
  }

  // -------------------------------------------------------------------------
  // Inserción y desplazamiento
  // -------------------------------------------------------------------------

  function agregar(nodo) {
    hilo.append(nodo);
    irAlFinal(hilo, true);
  }

  /** Una respuesta larga se muestra desde su inicio; una corta, al final del hilo. */
  function agregarRespuesta(nodo) {
    hilo.append(nodo);
    const cabe = nodo.offsetHeight <= hilo.clientHeight - 32;
    desplazarA(hilo, cabe ? hilo.scrollHeight : nodo.offsetTop - 16, true);
  }

  // -------------------------------------------------------------------------
  // Bienvenida
  // -------------------------------------------------------------------------

  function pintarBienvenida() {
    const sugerencias = crear(
      'ul',
      { clase: 'sugerencias', atributos: { 'aria-label': 'Preguntas de ejemplo' } },
      SUGERENCIAS.map((texto) =>
        crear('li', {}, crear('button', { clase: 'sugerencia', texto, atributos: { type: 'button' } })),
      ),
    );
    hilo.append(
      crear('div', { clase: 'bienvenida' }, [
        crear('p', { clase: 'bienvenida-titulo', texto: 'Tu librero virtual' }),
        crear('p', {
          texto: 'Cuéntame qué te gusta leer y te recomiendo libros del catálogo. También puedo '
            + 'reservarlos, cobrarlos y responder dudas de envíos, cambios y devoluciones.',
        }),
        sugerencias,
      ]),
    );
  }

  function quitarBienvenida() {
    hilo.querySelector('.bienvenida')?.remove();
  }

  // -------------------------------------------------------------------------
  // Burbujas
  // -------------------------------------------------------------------------

  function burbujaCliente(texto) {
    return crear('div', { clase: 'msg u' }, [
      crear('span', { clase: 'solo-lectores', texto: 'Tú: ' }),
      crear('div', { clase: 'msg-texto', texto }),
    ]);
  }

  function burbujaAsistente(d) {
    const texto = crear('div', { clase: 'msg-texto' });
    if (d.respuesta) ponerHtmlFormateado(texto, d.respuesta);
    else texto.append(crear('span', { clase: 'tenue', texto: '(respuesta vacía)' }));

    const nodo = crear('div', { clase: 'msg b' }, [
      crear('span', { clase: 'solo-lectores', texto: 'Asistente: ' }),
      texto,
    ]);
    if (d.aviso) nodo.append(crear('p', { clase: 'msg-aviso', texto: d.aviso }));
    const libros = tarjetasDeLibros(d.libros);
    if (libros) nodo.append(libros);
    const carrito = tarjetaCarrito(d.carrito);
    if (carrito) nodo.append(carrito);

    const chips = chipsDe(d, turnoFiltrado);
    if (chips.length) {
      nodo.append(crear('ul', { clase: 'datos', atributos: { 'aria-label': 'Detalles de la respuesta' } }, chips));
    }
    nodo.append(detalleJson(d));
    return nodo;
  }

  function burbujaError(error) {
    let texto;
    if (error instanceof ErrorApi) {
      texto = error.status === 422 ? `El mensaje no se pudo procesar: ${error.mensaje}` : error.mensaje;
    } else {
      console.error(error);
      texto = 'Ocurrió un error inesperado al mostrar la respuesta.';
    }
    return crear('div', { clase: 'msg b error' }, [
      crear('span', { clase: 'solo-lectores', texto: 'Aviso: ' }),
      crear('div', { clase: 'msg-texto', texto }),
    ]);
  }

  function indicadorEscribiendo() {
    return crear('div', { clase: 'msg b escribiendo' }, [
      crear('span', { clase: 'solo-lectores', texto: 'El asistente está escribiendo…' }),
      crear('span', { clase: 'puntos', atributos: { 'aria-hidden': 'true' } }, [
        crear('span'),
        crear('span'),
        crear('span'),
      ]),
    ]);
  }

  /** Muestra el indicador tras una breve demora; devuelve la función que lo quita. */
  function mostrarEscribiendo() {
    let nodo = null;
    const temporizador = setTimeout(() => {
      nodo = indicadorEscribiendo();
      agregar(nodo);
    }, DEMORA_INDICADOR_MS);
    return () => {
      clearTimeout(temporizador);
      nodo?.remove();
    };
  }

  // -------------------------------------------------------------------------
  // Envío
  // -------------------------------------------------------------------------

  // Lo escrito por partes se junta (agrupador.js) y sale como un solo turno; lo que se
  // escribe mientras el asistente responde espera en la cola y sale en el turno siguiente.
  const cola = [];
  const ayuda = porId('ayuda-entrada');
  const textoAyuda = ayuda.textContent;
  const agrupador = crearAgrupador({
    alEnviar: (texto) => {
      cola.push(texto);
      procesarCola();
    },
    alCambiar: (pendiente) => {
      ayuda.textContent = pendiente
        ? 'Esperando por si sigues escribiendo · Enter con la caja vacía lo envía ya'
        : textoAyuda;
    },
  });

  function procesarCola() {
    if (enviando || segundosBloqueo() > 0 || !cola.length) return;
    pedirRespuesta(cola.splice(0).join('\n'));
  }

  /** Muestra el mensaje y lo encola; con agrupar=true espera una pausa por si sigue otro. */
  function enviar(textoCrudo, { agrupar = false } = {}) {
    const texto = String(textoCrudo ?? '').trim();
    if (!texto || segundosBloqueo() > 0) return false;
    quitarBienvenida();
    agregar(burbujaCliente(texto));
    agrupador.agregar(texto);
    if (!agrupar) agrupador.vaciar();
    return true;
  }

  async function pedirRespuesta(texto) {
    const usuario = obtenerUsuario();
    enviando = true;
    actualizarBoton();
    const quitarIndicador = mostrarEscribiendo();

    try {
      const d = (await enviarMensaje({ mensaje: texto, usuarioId: usuario, sessionId: usuario })) ?? {};
      quitarIndicador();
      agregarRespuesta(burbujaAsistente(d));
      try {
        alResponder?.(d);
      } catch (error) {
        console.error(error);
      }
      return true;
    } catch (error) {
      quitarIndicador();
      agregar(burbujaError(error));
      // El texto vuelve a la caja para reenviarlo o corregirlo, salvo que ya se esté escribiendo otro.
      if (!entrada.value.trim()) entrada.value = texto;
      if (error instanceof ErrorApi && error.status === 429 && error.reintentarEn) {
        bloquearDurante(error.reintentarEn);
      }
      return false;
    } finally {
      enviando = false;
      actualizarBoton();
      const activo = document.activeElement;
      if (!activo || activo === document.body || activo === entrada) entrada.focus({ preventScroll: true });
      procesarCola();
    }
  }

  function enviarEntrada() {
    if (!entrada.value.trim()) return agrupador.vaciar(); // Enter sin texto: enviar ya lo escrito
    if (segundosBloqueo() > 0) return;
    const texto = entrada.value;
    entrada.value = '';
    enviar(texto, { agrupar: true });
  }

  entrada.addEventListener('input', () => agrupador.escribiendo());

  formulario.addEventListener('submit', (ev) => {
    ev.preventDefault();
    enviarEntrada();
  });

  // Enter envía; Shift+Enter inserta un salto de línea; durante la composición (IME) no se envía.
  entrada.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' || ev.shiftKey || ev.isComposing || ev.keyCode === 229) return;
    ev.preventDefault();
    enviarEntrada();
  });

  // Delegación: sugerencias y chips de turno.
  hilo.addEventListener('click', (ev) => {
    const objetivo = ev.target instanceof Element ? ev.target : null;
    if (!objetivo) return;

    const sugerencia = objetivo.closest('.sugerencia');
    if (sugerencia) {
      entrada.focus({ preventScroll: true });
      enviar(sugerencia.textContent);
      return;
    }

    const turno = objetivo.closest('button.dato.turno');
    if (turno) {
      const id = turno.dataset.turno || '';
      alFiltrarTurno?.(id === turnoFiltrado ? '' : id);
    }
  });

  // -------------------------------------------------------------------------
  // API pública del módulo
  // -------------------------------------------------------------------------

  /** Vacía el hilo y vuelve a mostrar la bienvenida. */
  function limpiar() {
    agrupador.descartar();
    cola.length = 0;
    vaciar(hilo);
    pintarBienvenida();
  }

  /** Nota centrada en el hilo (cambio de cliente, conversación borrada, errores del panel). */
  function anotar(texto, tipo = 'info') {
    agregar(crear('p', { clase: ['nota', tipo === 'error' && 'error'], texto }));
  }

  /** Sincroniza aria-pressed de los chips de turno con el filtro del panel de logs. */
  function marcarTurno(turnoId) {
    turnoFiltrado = turnoId || '';
    for (const b of hilo.querySelectorAll('button.dato.turno')) {
      b.setAttribute('aria-pressed', String(turnoFiltrado !== '' && b.dataset.turno === turnoFiltrado));
    }
  }

  pintarBienvenida();
  actualizarBoton();

  return { enviar, limpiar, anotar, marcarTurno };
}
