/**
 * logs.js — panel de logs en vivo y línea de métricas.
 *
 * - main.js llama a refrescarSiActivo() cada 3 s; aquí se decide si toca
 *   (pausa, pestaña oculta o falta de token).
 * - Filtros: nivel mínimo (select, recordado en localStorage) y turno
 *   (desde los chips del chat o desde cada evento).
 * - Si el backend responde 401/403, en lugar de un error se muestra un
 *   formulario para ingresar el token de administración.
 */

import { leerLogs, leerMetricas, obtenerTokenAdmin, guardarTokenAdmin, borrarTokenAdmin } from './api.js';
import {
  CLAVES,
  crear,
  porId,
  leerLocal,
  guardarLocal,
  ponerTexto,
  cercaDelFinal,
  irAlFinal,
  haySeleccionDentro,
  prefiereMenosMovimiento,
  formatearHora,
  formatearMs,
  formatearCantidad,
  plural,
} from './util.js';

const EVENTOS_POR_PEDIDO = 60;
const CAMPOS_FIJOS = new Set(['ts', 'nivel', 'evento', 'turno']);

function valorLegible(valor) {
  if (typeof valor === 'object') return JSON.stringify(valor);
  return String(valor);
}

/**
 * @param {object} opciones
 * @param {(turnoId: string) => void} [opciones.alCambiarTurno]  avisa al chat para marcar sus chips
 */
export function iniciarLogs({ alCambiarTurno } = {}) {
  const panel = porId('panel-logs');
  const caja = porId('logs');
  const selectNivel = porId('nivel');
  const botonAuto = porId('auto');
  const indicadorVivo = porId('en-vivo');
  const barraFiltro = porId('filtro-turno');
  const textoFiltro = porId('filtro-turno-id');
  const botonLimpiar = porId('limpiar');
  const formToken = porId('form-token');
  const inputToken = porId('token-admin');
  const errorToken = porId('token-error');
  const cajaMetricas = porId('metricas');
  const estadoLogs = porId('estado-logs');
  const botonOlvidar = porId('olvidar-token');

  let filtroTurno = '';
  let autoRefresco = true;
  let requiereToken = false;
  let intentoManualToken = false;
  let enCurso = false;
  let repetir = false;
  let firmaAnterior = '';
  let bajarAlPintar = true;

  const nivelGuardado = leerLocal(CLAVES.nivelLogs, '');
  if ([...selectNivel.options].some((o) => o.value === nivelGuardado)) selectNivel.value = nivelGuardado;

  // -------------------------------------------------------------------------
  // Indicadores
  // -------------------------------------------------------------------------

  function actualizarIndicador() {
    const vivo = autoRefresco && !requiereToken;
    indicadorVivo.dataset.activo = String(vivo);
    ponerTexto(indicadorVivo, requiereToken ? 'sin acceso' : autoRefresco ? 'en vivo' : 'en pausa');
    ponerTexto(botonAuto, autoRefresco ? 'Pausar' : 'Reanudar');
  }

  function marcarEstado(texto, esError = false) {
    ponerTexto(estadoLogs, texto);
    estadoLogs.classList.toggle('error', esError);
  }

  // -------------------------------------------------------------------------
  // Token de administración
  // -------------------------------------------------------------------------

  function pedirToken() {
    const habiaToken = Boolean(obtenerTokenAdmin());
    if (habiaToken) borrarTokenAdmin(); // el guardado ya no sirve

    requiereToken = true;
    formToken.hidden = false;
    caja.hidden = true;
    botonOlvidar.hidden = true;
    errorToken.hidden = !habiaToken;
    ponerTexto(errorToken, habiaToken ? 'El token no es válido. Revisa el valor configurado en el servidor.' : '');
    ponerTexto(cajaMetricas, 'Métricas no disponibles sin token de administración.');
    marcarEstado('');
    actualizarIndicador();

    if (intentoManualToken) inputToken.focus();
    intentoManualToken = false;
  }

  function ocultarFormToken() {
    const teniaFoco = formToken.contains(document.activeElement);
    requiereToken = false;
    formToken.hidden = true;
    errorToken.hidden = true;
    caja.hidden = false;
    if (teniaFoco) caja.focus();
    actualizarIndicador();
  }

  formToken.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const valor = inputToken.value.trim();
    if (!valor) {
      errorToken.hidden = false;
      ponerTexto(errorToken, 'Ingresa el token de administración.');
      inputToken.focus();
      return;
    }
    if (!guardarTokenAdmin(valor)) {
      errorToken.hidden = false;
      ponerTexto(errorToken, 'El navegador no permite guardar el token (almacenamiento bloqueado).');
      return;
    }
    inputToken.value = '';
    intentoManualToken = true;
    firmaAnterior = '';
    bajarAlPintar = true;
    refrescar();
  });

  botonOlvidar.addEventListener('click', () => {
    borrarTokenAdmin();
    botonOlvidar.hidden = true;
    firmaAnterior = '';
    refrescar();
  });

  // -------------------------------------------------------------------------
  // Eventos
  // -------------------------------------------------------------------------

  function filaEvento(e) {
    const nivel = String(e?.nivel ?? '').toUpperCase().replace(/[^A-Z]/g, '');
    const campos = Object.entries(e ?? {})
      .filter(([k, v]) => !CAMPOS_FIJOS.has(k) && v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${valorLegible(v)}`)
      .join(' ');

    const contenido = [];
    if (e.turno) {
      const activo = e.turno === filtroTurno;
      contenido.push(
        crear('button', {
          clase: 'ev-turno',
          texto: e.turno,
          atributos: {
            type: 'button',
            'aria-pressed': String(activo),
            title: activo ? 'Quitar el filtro de turno' : 'Filtrar por este turno',
          },
          datos: { turno: e.turno },
        }),
        ' ',
      );
    }
    contenido.push(campos);

    return crear('li', { clase: ['ev', nivel && `nivel-${nivel.toLowerCase()}`, filtroTurno && e.turno === filtroTurno && 'turno-activo'] }, [
      crear('time', { clase: 'hora', texto: formatearHora(e.ts), atributos: { datetime: e.ts || null } }),
      crear('span', { clase: ['niv', nivel && `niv-${nivel}`], texto: nivel || '—' }),
      crear('span', { clase: 'evn', texto: e.evento ?? '' }),
      crear('span', { clase: 'campos', atributos: { title: campos || null } }, contenido),
    ]);
  }

  function pintarEventos(d) {
    const eventos = Array.isArray(d?.eventos) ? d.eventos : [];
    const firma = [
      selectNivel.value,
      filtroTurno,
      eventos.length,
      eventos[0]?.ts ?? '',
      eventos[eventos.length - 1]?.ts ?? '',
      d?.total ?? '',
    ].join('|');
    if (firma === firmaAnterior) return;

    // No re-pintar mientras se usa un botón de la lista o se selecciona texto para copiar.
    const activo = document.activeElement;
    if (activo && activo !== caja && caja.contains(activo)) return;
    if (haySeleccionDentro(caja)) return;

    firmaAnterior = firma;
    const bajar = bajarAlPintar || cercaDelFinal(caja);
    bajarAlPintar = false;

    // El backend entrega del más nuevo al más viejo; se muestran en orden cronológico.
    const filas = eventos.slice().reverse().map(filaEvento);
    if (!filas.length) {
      filas.push(crear('li', { clase: 'ev-vacio', texto: filtroTurno ? 'Sin eventos para este turno.' : 'Sin eventos todavía.' }));
    }
    caja.replaceChildren(...filas);
    if (bajar) irAlFinal(caja);
  }

  function pintarMetricas(d) {
    const rutas = Array.isArray(d?.por_ruta) ? d.por_ruta : [];
    const resumenRutas = rutas.map((r) => `${r.ruta}: ${r.turnos}`).join(' · ') || 'sin turnos';
    const texto = `${plural(formatearCantidad(d?.turnos), 'turno')} · tokens ${formatearCantidad(d?.tokens_entrada)}/${formatearCantidad(d?.tokens_salida)}`
      + ` · p50 ${formatearMs(d?.latencia_p50)} · p95 ${formatearMs(d?.latencia_p95)} | ${resumenRutas}`;
    ponerTexto(cajaMetricas, texto);
    cajaMetricas.title = rutas
      .map((r) => `${r.ruta}: ${r.turnos} turnos, tokens ${r.entrada ?? '—'}/${r.salida ?? '—'}, latencia ${formatearMs(r.latencia)}`)
      .join('\n');
  }

  // -------------------------------------------------------------------------
  // Carga
  // -------------------------------------------------------------------------

  /** Pide logs y métricas ahora mismo (ignora la pausa: se usa para acciones del usuario). */
  async function refrescar() {
    if (enCurso) {
      repetir = true;
      return;
    }
    enCurso = true;
    try {
      const [logs, metricas] = await Promise.allSettled([
        leerLogs({ n: EVENTOS_POR_PEDIDO, nivel: selectNivel.value, turno: filtroTurno }),
        leerMetricas(),
      ]);

      if ([logs, metricas].some((r) => r.status === 'rejected' && r.reason?.sinPermiso)) {
        pedirToken();
        return;
      }
      if (requiereToken) ocultarFormToken();
      botonOlvidar.hidden = !obtenerTokenAdmin();

      if (logs.status === 'fulfilled') {
        pintarEventos(logs.value);
        const minimo = logs.value?.nivel_minimo;
        marcarEstado(minimo ? `El servidor registra desde ${minimo}.` : '');
      } else {
        const hora = new Date().toLocaleTimeString('es', { hour12: false });
        marcarEstado(`No se pudo actualizar el log (${hora}). ${logs.reason?.mensaje ?? ''}`.trim(), true);
      }
      if (metricas.status === 'fulfilled') pintarMetricas(metricas.value);
    } finally {
      enCurso = false;
      if (repetir) {
        repetir = false;
        refrescar();
      }
    }
  }

  /** Llamado por el temporizador: respeta pausa, pestaña oculta y falta de token. */
  function refrescarSiActivo() {
    if (!autoRefresco || requiereToken || document.hidden) return;
    refrescar();
  }

  // -------------------------------------------------------------------------
  // Filtros
  // -------------------------------------------------------------------------

  function mostrarPanelSiNoSeVe() {
    const r = panel.getBoundingClientRect();
    if (r.top >= window.innerHeight || r.bottom <= 0) {
      panel.scrollIntoView({ behavior: prefiereMenosMovimiento() ? 'auto' : 'smooth', block: 'start' });
    }
  }

  /** Filtra por un turno ('' quita el filtro). */
  function filtrarTurno(turnoId) {
    const nuevo = turnoId ? String(turnoId) : '';
    if (nuevo === filtroTurno) return;
    filtroTurno = nuevo;
    barraFiltro.hidden = !filtroTurno;
    ponerTexto(textoFiltro, filtroTurno);
    firmaAnterior = '';
    bajarAlPintar = true;
    alCambiarTurno?.(filtroTurno);
    if (filtroTurno) mostrarPanelSiNoSeVe();
    refrescar();
  }

  selectNivel.addEventListener('change', () => {
    guardarLocal(CLAVES.nivelLogs, selectNivel.value);
    firmaAnterior = '';
    bajarAlPintar = true;
    refrescar();
  });

  botonLimpiar.addEventListener('click', () => {
    filtrarTurno('');
    caja.focus({ preventScroll: true }); // el botón desaparece; el foco no se pierde
  });

  botonAuto.addEventListener('click', () => {
    autoRefresco = !autoRefresco;
    actualizarIndicador();
    if (autoRefresco) refrescar();
  });

  caja.addEventListener('click', (ev) => {
    const boton = ev.target instanceof Element ? ev.target.closest('.ev-turno') : null;
    if (!boton) return;
    const id = boton.dataset.turno || '';
    // El botón se va a re-pintar: el foco pasa a la lista para no perderse.
    caja.focus({ preventScroll: true });
    filtrarTurno(id === filtroTurno ? '' : id);
  });

  actualizarIndicador();

  return {
    refrescar,
    refrescarSiActivo,
    filtrarTurno,
    turnoActual: () => filtroTurno,
  };
}
