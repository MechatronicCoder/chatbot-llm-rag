/**
 * detalles.js — chips de metadatos y JSON completo de cada respuesta del chat.
 *
 * Muestran cómo se resolvió el turno: ruta, modo, latencia, modelo, tokens, FAQ,
 * herramientas con sus argumentos, documentos citados, pedidos y pagos, y un chip
 * para filtrar el log por ese turno. Todo se inserta como texto.
 */

import { crear, formatearMs, formatearPuntaje, resumir } from './util.js';


function chip(texto, clase = '', titulo = '') {
  return crear('li', { clase: ['dato', clase], texto, atributos: { title: titulo || null } });
}

/** En Markdown la "página" es la sección del documento. */
function etiquetaPagina(doc) {
  const esMarkdown = /\.md$/i.test(String(doc.documento ?? ''));
  return `${esMarkdown ? 'secc.' : 'pág.'} ${doc.pagina ?? '—'}`;
}

function nombreDocumento(ruta) {
  const base = String(ruta ?? '').split(/[\\/]/).pop() || 'documento';
  return base.replace(/\.[a-z0-9]+$/i, '').replace(/_/g, ' ');
}

function chipTurno(d, turnoFiltrado) {
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

/** Chips de ruta, modo, latencia, modelo, tokens, FAQ, herramientas, documentos y pedidos. */
export function chipsDe(d, turnoFiltrado = '') {
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
    chips.push(chip(`${nombreDocumento(doc.documento)} · ${etiquetaPagina(doc)} · ${formatearPuntaje(doc.score)}`, 'documento', titulo));
  }

  for (const id of Array.isArray(d.pedidos) ? d.pedidos : []) chips.push(chip(`pedido ${id}`, 'pedido'));
  for (const id of Array.isArray(d.pagos) ? d.pagos : []) chips.push(chip(`pago ${id}`, 'pago'));

  if (d.turno_id) chips.push(chipTurno(d, turnoFiltrado));
  return chips;
}

/** Bloque plegable con la respuesta completa de la API. */
export function detalleJson(d) {
  return crear('details', { clase: 'json' }, [
    crear('summary', { texto: 'Ver respuesta completa' }),
    crear('pre', {}, crear('code', { texto: JSON.stringify(d, null, 2) })),
  ]);
}
