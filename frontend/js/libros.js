/**
 * libros.js — tarjetas de los libros que muestra una respuesta del chat.
 *
 * Cada tarjeta lleva título, autor y una línea con precio, stock, valoración
 * y edad recomendada; la sinopsis va en el title. Todo el contenido se inserta
 * como texto, nunca como HTML.
 */

import { crear, formatearPrecio } from './util.js';

/** Lista de tarjetas, o null si el turno no mostró libros. */
export function tarjetasDeLibros(libros) {
  if (!Array.isArray(libros) || !libros.length) return null;
  return crear(
    'ul',
    { clase: 'libros', atributos: { 'aria-label': 'Libros de esta respuesta' } },
    libros.map((lb) => {
      const stock = Number(lb.stock);
      const disponibilidad = Number.isFinite(stock) && stock <= 0 ? 'agotado' : `${lb.stock} en stock`;
      const valoracion = lb.valoracion ? `★ ${lb.valoracion} (${lb.num_valoraciones})` : '';
      const datos = [formatearPrecio(lb.precio), disponibilidad, valoracion, lb.edad].filter(Boolean);
      return crear('li', { clase: 'libro', atributos: { title: lb.sinopsis || null } }, [
        crear('strong', { clase: 'libro-titulo', texto: lb.titulo }),
        crear('span', { clase: 'libro-autor', texto: lb.autor }),
        crear('span', { clase: 'libro-datos', texto: datos.join(' · ') }),
      ]);
    }),
  );
}
