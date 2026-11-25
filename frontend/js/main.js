/**
 * main.js — punto de entrada.
 *
 * - Inicia los módulos (panel, logs, chat) y los conecta entre sí.
 * - Pinta el estado del servicio en la cabecera a partir de /api/health.
 * - Mientras el estado sea "iniciando" (o el servidor no responda) reintenta
 *   con más frecuencia; al pasar a "ok" recarga catálogo, pedidos y logs.
 * - Mantiene los temporizadores: logs y métricas cada 3 s, salud cada 10 s.
 * - Si el backend pide iniciar sesión, muestra la pantalla de acceso (acceso.js).
 */

import { configurarAvisoDeAcceso, salud } from './api.js';
import { iniciarAcceso } from './acceso.js';
import { iniciarChat } from './chat.js';
import { iniciarPanel } from './panel.js';
import { iniciarLogs } from './logs.js';
import {
  crear,
  porId,
  ponerTexto,
  configurarMoneda,
  formatearCantidad,
  formatearDuracion,
  plural,
} from './util.js';

const INTERVALO_LOGS_MS = 3000;
const INTERVALO_SALUD_MS = 10000;
const REINTENTO_INICIANDO_MS = 1500;
const REINTENTO_MAXIMO_MS = 15000;

// ---------------------------------------------------------------------------
// Cabecera: estado del servicio
// ---------------------------------------------------------------------------

const indicador = porId('estado-indicador');
const meta = porId('estado-meta');

function ponerIndicador(estado, texto, titulo = '') {
  if (indicador.dataset.estado !== estado) indicador.dataset.estado = estado;
  ponerTexto(indicador, texto);
  indicador.title = titulo;
}

/** <li>etiqueta <strong>valor</strong></li> */
function datoMeta(etiqueta, valor, clase = '') {
  return crear('li', { clase }, [`${etiqueta} `, crear('strong', { texto: valor ?? '—' })]);
}

function pintarSalud(d) {
  const corpus = d?.corpus ?? {};
  if (d?.tienda) {
    configurarMoneda(d.tienda.moneda);
    if (d.tienda.nombre) {
      ponerTexto(porId('marca-nombre'), d.tienda.nombre);
      document.title = `${d.tienda.nombre} · Asistente`;
    }
  }

  if (d?.estado === 'ok') {
    ponerIndicador('ok', 'En línea', `Activo desde hace ${formatearDuracion(d.uptime_s)}`);
  } else {
    ponerIndicador('iniciando', 'Iniciando…', 'El servidor está cargando datos e índices');
  }

  const indexado = corpus.indexado ? `listo${corpus.motor ? ` (${corpus.motor})` : ''}` : 'indexando…';
  const conteos = [
    plural(formatearCantidad(d?.libros), 'libro'),
    plural(formatearCantidad(d?.pedidos), 'pedido'),
    plural(formatearCantidad(d?.pagos), 'pago'),
    plural(formatearCantidad(d?.mensajes), 'mensaje'),
  ].join(' · ');

  const items = [
    datoMeta('modelo', d?.modelo),
    datoMeta('modo', d?.modo, d?.modo === 'demo' ? 'modo-demo' : ''),
    crear('li', { atributos: { title: `${formatearCantidad(corpus.documentos)} documentos` } }, [
      'corpus ',
      crear('strong', { texto: formatearCantidad(corpus.chunks) }),
      ` chunks · ${indexado}`,
    ]),
    crear('li', {
      texto: conteos,
      atributos: { title: `${formatearCantidad(d?.usuarios)} usuarios · ${formatearCantidad(d?.unidades)} ejemplares` },
    }),
  ];
  if (corpus.aviso) items.push(crear('li', { clase: 'aviso-corpus', texto: corpus.aviso }));
  if (d?.version) items.push(crear('li', { clase: 'version', texto: `v${d.version}` }));

  meta.replaceChildren(...items);
}

// ---------------------------------------------------------------------------
// Módulos
// ---------------------------------------------------------------------------

/* Los módulos se referencian entre sí solo dentro de callbacks, que se ejecutan
   después de que los tres ya están creados. */
let chat;
let logs;
let panel;

function alResponder(d) {
  revisarSalud();
  panel.cargarPedidos({ resaltar: Array.isArray(d?.pedidos) ? d.pedidos : [] });
  panel.cargarCatalogo(); // el stock pudo cambiar
  logs.refrescarSiActivo();
}

function alBorrar({ ok, sesion, borrados, error }) {
  if (ok) {
    chat.limpiar();
    const detalle = Number.isFinite(borrados) ? ` (${plural(borrados, 'mensaje')} eliminados en el servidor)` : '';
    chat.anotar(`Conversación de «${sesion}» borrada${detalle}.`);
    logs.refrescarSiActivo();
    revisarSalud();
  } else {
    chat.anotar(`No se pudo borrar la conversación. ${error?.mensaje ?? ''}`.trim(), 'error');
  }
}

panel = iniciarPanel({
  alCambiarUsuario: (nuevo) => chat.anotar(`Sesión cambiada a «${nuevo}». Los mensajes anteriores pertenecen a otra sesión.`),
  alRecargar: () => Promise.allSettled([revisarSalud(), logs.refrescar()]),
  alBorrar,
});

logs = iniciarLogs({
  alCambiarTurno: (turnoId) => chat.marcarTurno(turnoId),
});

chat = iniciarChat({
  obtenerUsuario: () => panel.usuario(),
  alFiltrarTurno: (turnoId) => logs.filtrarTurno(turnoId),
  alResponder,
});

const acceso = iniciarAcceso({
  alEntrar: () => alQuedarListo(),
  alSalir: () => chat.limpiar(),
});
configurarAvisoDeAcceso(() => acceso.sesionRechazada());

// ---------------------------------------------------------------------------
// Bucle de salud
// ---------------------------------------------------------------------------

let temporizadorSalud = null;
let generacionSalud = 0;
let estadoAnterior = null; // null | 'ok' | 'iniciando' | 'error'
let fallosSeguidos = 0;

function alQuedarListo() {
  panel.cargarCatalogo();
  panel.cargarPedidos();
  logs.refrescar();
}

async function revisarSalud() {
  const generacion = ++generacionSalud;
  clearTimeout(temporizadorSalud);
  let espera = INTERVALO_SALUD_MS;
  let estado;

  try {
    const d = await salud();
    if (generacion !== generacionSalud) return; // otra revisión más nueva tomó el relevo
    pintarSalud(d);
    estado = d?.estado === 'ok' ? 'ok' : 'iniciando';
    fallosSeguidos = 0;
  } catch (error) {
    if (generacion !== generacionSalud) return;
    if (error?.status === 503) {
      // Algunos despliegues responden 503 mientras arrancan.
      estado = 'iniciando';
      ponerIndicador('iniciando', 'Iniciando…', error.mensaje);
    } else {
      estado = 'error';
      fallosSeguidos += 1;
      ponerIndicador('error', 'Sin conexión', error?.mensaje ?? 'No se pudo consultar /api/health');
    }
  }

  const recienListo = estado === 'ok' && estadoAnterior !== null && estadoAnterior !== 'ok';
  if (recienListo && !acceso.bloqueado()) alQuedarListo();
  estadoAnterior = estado;

  if (estado === 'iniciando') espera = REINTENTO_INICIANDO_MS;
  if (estado === 'error') espera = Math.min(REINTENTO_INICIANDO_MS * 2 ** (fallosSeguidos - 1), REINTENTO_MAXIMO_MS);

  temporizadorSalud = setTimeout(revisarSalud, espera);
}

// ---------------------------------------------------------------------------
// Arranque
// ---------------------------------------------------------------------------

/* Desde la tienda (tienda.html), "Comprar" abre el chat con ?pedir=<isbn>: el pedido
   queda escrito en la caja, sin enviarse, para que la persona lo revise o lo complete. */
function prellenarPedido() {
  const url = new URL(location.href);
  const isbn = url.searchParams.get('pedir');
  if (!isbn) return;
  url.searchParams.delete('pedir');
  history.replaceState(null, '', url.pathname + url.search + url.hash);
  if (!/^[0-9-]{10,17}$/.test(isbn)) return;
  const entrada = porId('entrada');
  if (!entrada.value.trim()) entrada.value = `Quiero comprar el libro con ISBN ${isbn}.`;
  entrada.focus();
}

prellenarPedido();
revisarSalud();
// Con el acceso activado, los datos se cargan recién al iniciar sesión (alEntrar).
acceso.verificar().then((d) => {
  if (!d?.requerido || d.autenticado) alQuedarListo();
});

setInterval(() => {
  if (!acceso.bloqueado()) logs.refrescarSiActivo();
}, INTERVALO_LOGS_MS);

document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  if (!acceso.bloqueado()) logs.refrescarSiActivo();
  revisarSalud();
});
