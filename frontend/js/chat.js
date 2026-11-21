/**
 * chat.js — hilo de mensajes y formulario de envío.
 *
 * Pinta las burbujas del cliente y del asistente, los chips de metadatos de
 * cada respuesta, el bloque "ver respuesta completa" con el JSON, el
 * indicador de escritura y los errores. No conoce el panel ni los logs:
 * se comunica con ellos mediante las funciones que recibe en iniciarChat().
 */

import { enviarMensaje, ErrorApi } from './api.js';
import {
  crear,
  porId,
  vaciar,
  ponerHtmlFormateado,
  irAlFinal,
  desplazarA,
  formatearMs,
  formatearPuntaje,
  resumir,
} from './util.js';

const SUGERENCIAS = [
  '¿Tienen laptops?',
  'Quiero 2 Dell',
  'Págala con tarjeta',
  '¿Cuánto dura la garantía?',
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

  function actualizarBoton() {
    const restante = segundosBloqueo();
    boton.disabled = enviando || restante > 0;
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
        crear('p', { clase: 'bienvenida-titulo', texto: 'Asistente de TechStore' }),
        crear('p', {
          texto: 'Consulta el catálogo, arma un pedido, págalo o pregunta por envíos, garantías y devoluciones.',
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

    const chips = chipsDe(d);
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
  // Chips de metadatos
  // -------------------------------------------------------------------------

  function chip(texto, clase = '', titulo = '') {
    return crear('li', { clase: ['dato', clase], texto, atributos: { title: titulo || null } });
  }

  function nombreDocumento(ruta) {
    const base = String(ruta ?? '').split(/[\\/]/).pop() || 'documento';
    return base.replace(/\.[a-z0-9]+$/i, '').replace(/_/g, ' ');
  }

  function chipTurno(d) {
    const etiqueta = typeof d.turno === 'number' ? `turno ${d.turno} · ${d.turno_id}` : `turno ${d.turno_id}`;
    const boton = crear('button', {
      clase: 'dato turno',
      texto: etiqueta,
      atributos: {
        type: 'button',
        'aria-pressed': String(turnoFiltrado === d.turno_id),
        'aria-label': `Filtrar el log por el ${etiqueta}`,
        title: 'Filtrar el panel de logs por este turno',
      },
      datos: { turno: d.turno_id },
    });
    return crear('li', {}, boton);
  }

  function chipsDe(d) {
    const chips = [];

    if (d.ruta) chips.push(chip(`ruta ${d.ruta}`, 'ruta', d.fuente ? `Fuente: ${d.fuente}` : ''));
    if (d.modo) chips.push(chip(`modo ${d.modo}`, d.modo === 'demo' ? 'demo' : ''));
    if (d.latencia_ms !== null && d.latencia_ms !== undefined) {
      chips.push(chip(formatearMs(d.latencia_ms), '', 'Latencia del turno'));
    }
    if (d.modelo) chips.push(chip(d.modelo, '', d.endpoint ? `Endpoint: ${d.endpoint}` : 'Modelo'));
    if (Array.isArray(d.tokens) && d.tokens.some((n) => n !== null && n !== undefined)) {
      chips.push(chip(`tokens ${d.tokens[0] ?? 0}/${d.tokens[1] ?? 0}`, '', 'Tokens de entrada / salida'));
    }
    if (d.faq_id) {
      const similitud = d.similitud !== null && d.similitud !== undefined ? ` · ${formatearPuntaje(d.similitud)}` : '';
      chips.push(chip(`FAQ ${d.faq_id}${similitud}`, 'ruta', 'Pregunta frecuente y similitud'));
    }

    for (const h of Array.isArray(d.herramientas) ? d.herramientas : []) {
      const argumentos = h.argumentos ? `\nArgumentos: ${resumir(JSON.stringify(h.argumentos), 200)}` : '';
      const titulo = `${h.ok === false ? 'Falló' : 'Correcta'}${h.resumen ? ` · ${h.resumen}` : ''}${argumentos}`;
      chips.push(chip(`${h.nombre} ${formatearMs(h.ms)}`, ['herramienta', h.ok === false && 'fallo'].filter(Boolean).join(' '), titulo));
    }

    for (const doc of Array.isArray(d.documentos) ? d.documentos : []) {
      const titulo = `${doc.documento ?? ''}, página ${doc.pagina ?? '—'}${doc.chunk !== undefined ? `, fragmento ${doc.chunk}` : ''}`
        + (doc.texto ? `\n\n${resumir(doc.texto, 280)}` : '');
      chips.push(chip(`${nombreDocumento(doc.documento)} · pág. ${doc.pagina ?? '—'} · ${formatearPuntaje(doc.score)}`, 'documento', titulo));
    }

    for (const id of Array.isArray(d.pedidos) ? d.pedidos : []) chips.push(chip(`pedido ${id}`, 'pedido'));
    for (const id of Array.isArray(d.pagos) ? d.pagos : []) chips.push(chip(`pago ${id}`, 'pago'));

    if (d.turno_id) chips.push(chipTurno(d));
    return chips;
  }

  function detalleJson(d) {
    return crear('details', { clase: 'json' }, [
      crear('summary', { texto: 'Ver respuesta completa' }),
      crear('pre', {}, crear('code', { texto: JSON.stringify(d, null, 2) })),
    ]);
  }

  // -------------------------------------------------------------------------
  // Envío
  // -------------------------------------------------------------------------

  function puedeEnviar() {
    return !enviando && segundosBloqueo() === 0;
  }

  async function enviar(textoCrudo) {
    const texto = String(textoCrudo ?? '').trim();
    if (!texto || !puedeEnviar()) return false;

    const usuario = obtenerUsuario();
    quitarBienvenida();
    agregar(burbujaCliente(texto));
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
    }
  }

  function enviarEntrada() {
    if (!entrada.value.trim() || !puedeEnviar()) return;
    const texto = entrada.value;
    entrada.value = '';
    enviar(texto);
  }

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
