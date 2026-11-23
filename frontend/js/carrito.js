/**
 * carrito.js — tarjeta del carrito o del pedido que tocó una respuesta del chat.
 *
 * Los montos (importe de cada línea, subtotal, envío y total) llegan calculados
 * por el servidor: aquí solo se formatean, nunca se suman. Todo el contenido se
 * inserta como texto, nunca como HTML.
 */

import { crear, formatearPrecio } from './util.js';

const ETIQUETAS = Object.freeze({
  carrito: 'carrito',
  reservado: 'reservado',
  pagado: 'pagado',
  cancelado: 'cancelado',
});

function textoEnvio(c) {
  if (c.envio === null || c.envio === undefined) return 'falta elegir Lima o provincias';
  const destino = c.zona_nombre ? ` (${c.zona_nombre})` : '';
  return c.envio === 0 ? `gratis${destino}` : `${formatearPrecio(c.envio)}${destino}`;
}

function fila(etiqueta, valor, clase) {
  return crear('div', { clase: ['carrito-fila', clase] }, [
    crear('dt', { texto: etiqueta }),
    crear('dd', { texto: valor }),
  ]);
}

/** Tarjeta del pedido, o null si el turno no tocó ninguno. */
export function tarjetaCarrito(c) {
  if (!c || !Array.isArray(c.lineas) || !c.lineas.length) return null;
  const estado = ETIQUETAS[c.estado] ?? c.estado ?? '—';
  const lineas = crear(
    'ul',
    { clase: 'carrito-lineas' },
    c.lineas.map((l) =>
      crear('li', {}, [
        crear('span', { texto: `${l.cantidad} × ${l.titulo}` }),
        crear('span', { clase: 'carrito-importe', texto: formatearPrecio(l.importe) }),
      ]),
    ),
  );
  const totales = crear('dl', { clase: 'carrito-totales' }, [
    fila('Subtotal', formatearPrecio(c.subtotal)),
    fila('Envío', textoEnvio(c)),
    fila('Total', formatearPrecio(c.total), 'carrito-total'),
  ]);
  const hijos = [
    crear('div', { clase: 'carrito-cabecera' }, [
      crear('strong', { texto: c.pedido_id ?? 'Pedido' }),
      crear('span', { clase: 'carrito-estado', texto: estado }),
    ]),
    lineas,
    totales,
  ];
  if (c.falta_para_envio_gratis) {
    hijos.push(
      crear('p', {
        clase: 'carrito-nota',
        texto: `Faltan ${formatearPrecio(c.falta_para_envio_gratis)} para el envío gratis.`,
      }),
    );
  }
  return crear('section', { clase: 'carrito', atributos: { 'aria-label': `Pedido ${c.pedido_id ?? ''}, ${estado}` } }, hijos);
}
