/**
 * panel.js — columna central: cliente, pedidos y catálogo.
 *
 * - El id del cliente se guarda en localStorage y se usa también como session_id.
 * - "Recargar" vuelve a pedir pedidos y catálogo (y avisa a main.js para el resto).
 * - "Borrar conversación" llama a DELETE /api/sesiones/{id} y entrega el resultado
 *   a main.js, que decide cómo reflejarlo en el chat.
 */

import { pedidosDe, buscarLibros, borrarSesion } from './api.js';
import {
  CLAVES,
  crear,
  porId,
  leerLocal,
  guardarLocal,
  ponerTexto,
  formatearPrecio,
  plural,
} from './util.js';

const USUARIO_POR_DEFECTO = 'cliente-01';
const LIMITE_CATALOGO = 8;
const DEMORA_BUSQUEDA_MS = 300;

/** Estados de pedido conocidos → clase visual. "reservado" equivale a pendiente de pago. */
const CLASE_ESTADO = Object.freeze({
  carrito: 'pendiente',
  pendiente: 'pendiente',
  reservado: 'pendiente',
  pagado: 'pagado',
  cancelado: 'cancelado',
});

function claseEstado(estado) {
  const clave = String(estado ?? '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
  return CLASE_ESTADO[clave] ?? '';
}

/**
 * @param {object} opciones
 * @param {(nuevo: string, anterior: string) => void} [opciones.alCambiarUsuario]
 * @param {() => Promise<unknown> | void} [opciones.alRecargar]
 * @param {(resultado: {ok: boolean, sesion: string, borrados?: unknown, error?: Error}) => void} [opciones.alBorrar]
 */
export function iniciarPanel({ alCambiarUsuario, alRecargar, alBorrar } = {}) {
  const inputUsuario = porId('usuario');
  const botonRecargar = porId('recargar');
  const botonBorrar = porId('borrar');
  const cajaPedidos = porId('pedidos');
  const cajaCatalogo = porId('catalogo');
  const inputBuscar = porId('buscar-catalogo');
  const resumenCatalogo = porId('catalogo-resumen');

  inputUsuario.value = (leerLocal(CLAVES.usuario, '') || '').trim() || USUARIO_POR_DEFECTO;
  let usuarioActual = inputUsuario.value;

  // -------------------------------------------------------------------------
  // Utilidades de pintado
  // -------------------------------------------------------------------------

  function mensajeEn(caja, texto, esError = false) {
    caja.replaceChildren(crear('p', { clase: ['vacio', esError && 'error'], texto }));
  }

  /** Marca un botón como ocupado sin deshabilitarlo (deshabilitar haría perder el foco). */
  async function ocupado(boton, tarea) {
    if (boton.getAttribute('aria-busy') === 'true') return;
    boton.setAttribute('aria-busy', 'true');
    try {
      await tarea();
    } finally {
      boton.removeAttribute('aria-busy');
    }
  }

  // -------------------------------------------------------------------------
  // Cliente
  // -------------------------------------------------------------------------

  inputUsuario.addEventListener('change', () => {
    const nuevo = inputUsuario.value.trim() || USUARIO_POR_DEFECTO;
    inputUsuario.value = nuevo;
    if (nuevo === usuarioActual) return;
    const anterior = usuarioActual;
    usuarioActual = nuevo;
    guardarLocal(CLAVES.usuario, nuevo);
    cargarPedidos();
    alCambiarUsuario?.(nuevo, anterior);
  });

  botonRecargar.addEventListener('click', () =>
    ocupado(botonRecargar, () => Promise.allSettled([cargarPedidos(), cargarCatalogo(), alRecargar?.()])),
  );

  botonBorrar.addEventListener('click', () =>
    ocupado(botonBorrar, async () => {
      const sesion = usuarioActual;
      try {
        const d = await borrarSesion(sesion);
        alBorrar?.({ ok: true, sesion, borrados: d?.borrados });
      } catch (error) {
        alBorrar?.({ ok: false, sesion, error });
      }
    }),
  );

  // -------------------------------------------------------------------------
  // Pedidos
  // -------------------------------------------------------------------------

  let secuenciaPedidos = 0;

  function pintarPedidos(d, resaltar) {
    const pedidos = Array.isArray(d?.pedidos) ? d.pedidos : [];
    if (!pedidos.length) {
      mensajeEn(cajaPedidos, 'Sin pedidos.');
      return;
    }

    const lista = crear(
      'ul',
      { clase: 'lista' },
      pedidos.map((p) => {
        const estado = claseEstado(p.estado);
        const lineas = Array.isArray(p.lineas) ? p.lineas : [];
        const envio = p.envio === null || p.envio === undefined ? 'sin zona de envío'
          : `envío ${p.envio ? formatearPrecio(p.envio) : 'gratis'}`;
        const titulo = [
          lineas.map((l) => `${l.cantidad} × ${l.titulo} (${formatearPrecio(l.importe)})`).join(', '),
          envio,
          p.creado_en ? `Creado ${p.creado_en}` : '',
        ].filter(Boolean).join(' · ');
        const unidades = Number(p.unidades) || 0;
        return crear('li', { clase: ['item', resaltar.has(String(p.pedido_id)) && 'nuevo'], atributos: { title: titulo || null } }, [
          crear('div', { clase: 'item-titulo' }, [
            crear('strong', { texto: p.pedido_id ?? '—' }),
            crear('span', { clase: ['etiqueta-estado', estado && `estado-${estado}`], texto: p.estado ?? '—' }),
          ]),
          crear('div', { clase: 'item-sub', texto: `${unidades} ${unidades === 1 ? 'libro' : 'libros'} — ${formatearPrecio(p.total)}` }),
        ]);
      }),
    );

    const totales = crear('div', { clase: 'totales' }, [
      crear('span', {}, ['Pendiente ', crear('strong', { texto: formatearPrecio(d.pendiente) })]),
      crear('span', {}, ['Pagado ', crear('strong', { texto: formatearPrecio(d.pagado) })]),
    ]);

    cajaPedidos.replaceChildren(lista, totales);
  }

  /**
   * @param {{resaltar?: Array<string|number>}} [opciones] ids de pedidos recién creados
   */
  async function cargarPedidos({ resaltar = [] } = {}) {
    const turno = ++secuenciaPedidos;
    const usuarioId = usuarioActual;
    cajaPedidos.setAttribute('aria-busy', 'true');
    try {
      const d = await pedidosDe(usuarioId);
      if (turno !== secuenciaPedidos) return; // llegó una respuesta más nueva
      pintarPedidos(d, new Set(resaltar.map(String)));
    } catch (error) {
      if (turno !== secuenciaPedidos) return;
      mensajeEn(cajaPedidos, `No se pudieron cargar los pedidos. ${error?.mensaje ?? ''}`.trim(), true);
    } finally {
      if (turno === secuenciaPedidos) cajaPedidos.removeAttribute('aria-busy');
    }
  }

  // -------------------------------------------------------------------------
  // Catálogo
  // -------------------------------------------------------------------------

  let controlCatalogo = null;
  let temporizadorBusqueda = null;

  function pintarCatalogo(d, consulta) {
    const libros = Array.isArray(d?.libros) ? d.libros : [];
    const total = Number.isFinite(d?.total) ? d.total : libros.length;

    let resumen = plural(libros.length, 'libro');
    if (total > libros.length) resumen = `${libros.length} de ${total} libros`;
    if (consulta) resumen += ` para «${consulta}»`;
    ponerTexto(resumenCatalogo, resumen);

    if (!libros.length) {
      mensajeEn(cajaCatalogo, consulta ? 'No hay libros que coincidan.' : 'El catálogo está vacío.');
      return;
    }

    cajaCatalogo.replaceChildren(
      crear(
        'ul',
        { clase: 'lista' },
        libros.map((lb) => {
          const stock = Number(lb.stock);
          const agotado = Number.isFinite(stock) && stock <= 0;
          return crear('li', { clase: 'item', atributos: { title: lb.sinopsis || null } }, [
            crear('div', { clase: 'item-titulo' }, [
              crear('strong', { texto: lb.titulo ?? lb.isbn ?? '—' }),
              crear('span', { clase: 'precio', texto: formatearPrecio(lb.precio) }),
            ]),
            crear('div', { clase: 'item-sub' }, [
              [lb.autor, lb.categoria, lb.edad].filter(Boolean).join(' · '),
              ' · ',
              crear('span', { clase: agotado ? 'agotado' : '', texto: agotado ? 'agotado' : `${lb.stock ?? '?'} u.` }),
            ]),
          ]);
        }),
      ),
    );
  }

  async function cargarCatalogo() {
    controlCatalogo?.abort();
    const control = new AbortController();
    controlCatalogo = control;
    const consulta = inputBuscar.value.trim();
    cajaCatalogo.setAttribute('aria-busy', 'true');
    try {
      const d = await buscarLibros({ consulta, limite: LIMITE_CATALOGO }, { senal: control.signal });
      if (!control.signal.aborted) pintarCatalogo(d, consulta);
    } catch (error) {
      if (control.signal.aborted || error?.tipo === 'cancelada') return;
      ponerTexto(resumenCatalogo, '');
      mensajeEn(cajaCatalogo, `No se pudo cargar el catálogo. ${error?.mensaje ?? ''}`.trim(), true);
    } finally {
      if (controlCatalogo === control) {
        controlCatalogo = null;
        cajaCatalogo.removeAttribute('aria-busy');
      }
    }
  }

  inputBuscar.addEventListener('input', () => {
    clearTimeout(temporizadorBusqueda);
    temporizadorBusqueda = setTimeout(cargarCatalogo, DEMORA_BUSQUEDA_MS);
  });

  return {
    usuario: () => usuarioActual,
    cargarPedidos,
    cargarCatalogo,
  };
}
