/**
 * libros.js — tarjetas de los libros que muestra una respuesta del chat.
 *
 * Cada tarjeta lleva título, autor y una línea con precio, stock, valoración
 * y edad recomendada; la sinopsis va en el title. Todo el contenido se inserta
 * como texto, nunca como HTML.
 *
 * Cada tarjeta es un botón: elegirla envía al librero "Quiero el libro «…»", como si
 * el cliente lo hubiera escrito (el envío lo hace chat.js con data-pedido). Un libro
 * agotado se muestra, pero no se puede elegir.
 */

import { crear, formatearPrecio } from './util.js';

export const pedidoDe = (lb) => `Quiero el libro «${lb.titulo}»`;

/** Lista de tarjetas, o null si el turno no mostró libros. */
export function tarjetasDeLibros(libros) {
  if (!Array.isArray(libros) || !libros.length) return null;
  return crear(
    'ul',
    { clase: 'libros', atributos: { 'aria-label': 'Libros de esta respuesta: elige uno para pedirlo' } },
    libros.map((lb) => {
      const stock = Number(lb.stock);
      const agotado = Number.isFinite(stock) && stock <= 0;
      const disponibilidad = agotado ? 'agotado' : `${lb.stock} en stock`;
      const valoracion = lb.valoracion ? `★ ${lb.valoracion} (${lb.num_valoraciones})` : '';
      const datos = [formatearPrecio(lb.precio), disponibilidad, valoracion, lb.edad].filter(Boolean);
      return crear(
        'li',
        { clase: 'libro' },
        crear(
          'button',
          {
            clase: 'libro-boton',
            datos: { pedido: pedidoDe(lb) },
            atributos: {
              type: 'button',
              disabled: agotado,
              title: agotado ? 'Agotado por ahora' : lb.sinopsis || null,
            },
          },
          [
            crear('strong', { clase: 'libro-titulo', texto: lb.titulo }),
            crear('span', { clase: 'libro-autor', texto: lb.autor }),
            crear('span', { clase: 'libro-datos', texto: datos.join(' · ') }),
            crear('span', { clase: 'libro-elegir', texto: agotado ? 'Agotado' : 'Lo quiero', atributos: { 'aria-hidden': 'true' } }),
          ],
        ),
      );
    }),
  );
}
