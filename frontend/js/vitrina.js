/**
 * vitrina.js — tarjetas y ficha de los libros de la tienda (tienda.html).
 *
 * La portada se carga con `src = libro.portada` (enlace https o ruta dentro de
 * frontend/). Sin portada, o si la imagen no carga, se muestra una tapa
 * generada con el título y el autor. Todo el texto se inserta como texto,
 * nunca como HTML.
 */

import { crear, formatearPrecio, plural } from './util.js';

const POCAS_UNIDADES = 3;
const TONOS_TAPA = 6;

/** Enlace al chat con el pedido ya escrito (main.js lo pone en la caja, no lo envía). */
export function enlaceComprar(libro) {
  return `./?pedir=${encodeURIComponent(libro.isbn)}`;
}

function tonoDe(isbn) {
  let suma = 0;
  for (const c of String(isbn)) suma = (suma * 31 + c.charCodeAt(0)) % 9973;
  return suma % TONOS_TAPA;
}

/** Tapa de reemplazo: título y autor sobre un color fijo por libro. */
function tapaGenerada(libro) {
  return crear('div', { clase: ['tapa', `tapa-tono-${tonoDe(libro.isbn)}`], atributos: { 'aria-hidden': 'true' } }, [
    crear('span', { clase: 'tapa-titulo', texto: libro.titulo }),
    crear('span', { clase: 'tapa-autor', texto: libro.autor }),
  ]);
}

/** <img> de la portada, o la tapa generada si no hay o falla al cargar. */
export function portada(libro, { clase = 'portada', perezosa = true } = {}) {
  const marco = crear('div', { clase: `${clase}-marco` });
  if (!libro.portada) {
    marco.append(tapaGenerada(libro));
    return marco;
  }
  const img = crear('img', {
    clase,
    atributos: {
      src: libro.portada,
      alt: `Portada de ${libro.titulo}`,
      loading: perezosa ? 'lazy' : 'eager',
      decoding: 'async',
      referrerpolicy: 'no-referrer',
    },
  });
  img.addEventListener('error', () => img.replaceWith(tapaGenerada(libro)), { once: true });
  marco.append(img);
  return marco;
}

/** Etiqueta de disponibilidad: { texto, clase }. */
export function disponibilidad(stock) {
  const n = Number(stock);
  if (!Number.isFinite(n) || n <= 0) return { texto: 'Agotado', clase: 'agotado' };
  if (n <= POCAS_UNIDADES) return { texto: `¡Quedan ${n}!`, clase: 'pocas' };
  return { texto: 'Disponible', clase: 'disponible' };
}

/** "★★★★☆ 4.6 (132)" como nodo accesible, o null sin valoración. */
export function estrellas(valoracion, votos) {
  if (!valoracion) return null;
  const llenas = Math.round(Number(valoracion));
  return crear('span', { clase: 'estrellas', atributos: { title: `${valoracion} de 5, ${plural(votos, 'valoración', 'valoraciones')}` } }, [
    crear('span', { clase: 'estrellas-iconos', texto: '★'.repeat(llenas) + '☆'.repeat(5 - llenas), atributos: { 'aria-hidden': 'true' } }),
    crear('span', { clase: 'estrellas-valor', texto: `${valoracion} (${votos})` }),
  ]);
}

/** Tarjeta de la vitrina; `alAbrir(libro)` abre la ficha. */
export function tarjeta(libro, alAbrir) {
  const estado = disponibilidad(libro.stock);
  const agotado = estado.clase === 'agotado';
  const abrir = crear('button', { clase: 'tarjeta-abrir', atributos: { type: 'button', 'aria-label': `Ver ${libro.titulo}, de ${libro.autor}` } }, [
    portada(libro),
    crear('span', { clase: ['insignia', `insignia-${estado.clase}`], texto: estado.texto }),
  ]);
  abrir.addEventListener('click', () => alAbrir(libro));

  const comprar = agotado
    ? crear('span', { clase: 'boton suave tarjeta-comprar', texto: 'Sin stock', atributos: { 'aria-disabled': 'true' } })
    : crear('a', { clase: 'boton tarjeta-comprar', texto: 'Comprar', atributos: { href: enlaceComprar(libro), title: 'Pedir este libro al asistente' } });

  return crear('li', { clase: ['tarjeta', agotado && 'tarjeta-agotada'] }, [
    abrir,
    crear('div', { clase: 'tarjeta-cuerpo' }, [
      crear('span', { clase: 'tarjeta-categoria', texto: libro.genero || libro.categoria }),
      crear('h3', { clase: 'tarjeta-titulo', texto: libro.titulo, atributos: { title: libro.titulo } }),
      crear('p', { clase: 'tarjeta-autor', texto: libro.autor }),
      estrellas(libro.valoracion, libro.num_valoraciones),
      crear('div', { clase: 'tarjeta-pie' }, [
        crear('strong', { clase: 'tarjeta-precio', texto: formatearPrecio(libro.precio) }),
        comprar,
      ]),
    ]),
  ]);
}

function dato(etiqueta, valor) {
  if (valor === null || valor === undefined || valor === '') return null;
  return crear('div', { clase: 'ficha-dato' }, [crear('dt', { texto: etiqueta }), crear('dd', { texto: String(valor) })]);
}

function edadTexto(f) {
  if (f.edad_min === null && f.edad_max === null) return null;
  if (f.edad_max === null) return `desde ${f.edad_min} años`;
  return `${f.edad_min ?? 0}–${f.edad_max} años`;
}

/** Contenido de la ficha completa (respuesta de /api/libros/{isbn}). */
export function contenidoFicha(f) {
  const estado = disponibilidad(f.stock);
  const accion = estado.clase === 'agotado'
    ? crear('p', { clase: 'ficha-aviso', texto: 'Agotado por ahora. Pregúntale al asistente por libros parecidos.' })
    : crear('a', { clase: 'boton ficha-comprar', texto: 'Comprar con el asistente', atributos: { href: enlaceComprar(f) } });
  const temas = (f.temas ?? []).length
    ? crear('ul', { clase: 'ficha-temas', atributos: { 'aria-label': 'Temas' } }, f.temas.map((t) => crear('li', { texto: t })))
    : null;

  return [
    portada(f, { clase: 'ficha-portada', perezosa: false }),
    crear('div', { clase: 'ficha-info' }, [
      crear('span', { clase: 'tarjeta-categoria', texto: [f.categoria, f.genero].filter(Boolean).join(' · ') }),
      crear('h2', { clase: 'ficha-titulo', texto: f.titulo, atributos: { id: 'ficha-titulo' } }),
      crear('p', { clase: 'ficha-autor', texto: f.autor }),
      estrellas(f.valoracion, f.num_valoraciones),
      crear('div', { clase: 'ficha-compra' }, [
        crear('strong', { clase: 'ficha-precio', texto: formatearPrecio(f.precio) }),
        crear('span', { clase: ['insignia', 'insignia-linea', `insignia-${estado.clase}`], texto: estado.texto }),
      ]),
      accion,
      f.sinopsis ? crear('p', { clase: 'ficha-sinopsis', texto: f.sinopsis }) : null,
      temas,
      crear('dl', { clase: 'ficha-datos' }, [
        dato('Editorial', f.editorial),
        dato('Año', f.anio),
        dato('Formato', f.formato),
        dato('Páginas', f.paginas),
        dato('Idioma', f.idioma),
        dato('Edad', edadTexto(f)),
        dato('ISBN', f.isbn),
      ]),
    ]),
  ];
}
