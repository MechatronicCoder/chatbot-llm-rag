/**
 * tienda.js — punto de entrada de la vitrina (tienda.html).
 *
 * - Carga todos los libros de /api/catalogo una sola vez y filtra en el navegador:
 *   búsqueda por título, autor o ISBN (sin tildes), categoría, solo disponibles
 *   y orden. El filtro elegido queda en la URL (?q=&categoria=&orden=) para
 *   poder compartirla o volver atrás.
 * - Al abrir un libro pide su ficha completa a /api/libros/{isbn}.
 * - "Comprar" lleva al chat con el pedido escrito: el carrito lo maneja el asistente.
 * - Si la demo tiene acceso con clave y no hay sesión, avisa y enlaza al chat
 *   (allí está la pantalla de acceso).
 */

import { catalogoCompleto, categorias, configurarAvisoDeAcceso, estadoAcceso, fichaLibro, salud } from './api.js';
import { configurarMoneda, crear, plural, porId, ponerTexto } from './util.js';
import { contenidoFicha, tarjeta } from './vitrina.js';

const ESPERA_BUSQUEDA_MS = 150;

const vitrina = porId('vitrina');
const resultado = porId('resultado');
const buscar = porId('buscar');
const orden = porId('orden');
const soloDisponibles = porId('solo-disponibles');
const cajaCategorias = porId('categorias');
const ficha = porId('ficha');
const fichaCuerpo = porId('ficha-cuerpo');

let libros = [];
let categoriaActual = '';

/** Minúsculas y sin tildes: "García Márquez" → "garcia marquez". */
function normalizar(texto) {
  return String(texto ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
}

const ORDENES = {
  destacados: (a, b) => (b.valoracion ?? 0) - (a.valoracion ?? 0) || b.num_valoraciones - a.num_valoraciones,
  'precio-asc': (a, b) => a.precio - b.precio,
  'precio-desc': (a, b) => b.precio - a.precio,
  titulo: (a, b) => a.titulo.localeCompare(b.titulo, 'es'),
};

function filtrados() {
  const palabras = normalizar(buscar.value).split(/\s+/).filter(Boolean);
  const lista = libros.filter((lb) => {
    if (categoriaActual && lb.categoria !== categoriaActual) return false;
    if (soloDisponibles.checked && !(lb.stock > 0)) return false;
    return palabras.every((p) => lb._texto.includes(p));
  });
  return lista.sort(ORDENES[orden.value] ?? ORDENES.destacados);
}

function guardarEnUrl() {
  const p = new URLSearchParams();
  if (buscar.value.trim()) p.set('q', buscar.value.trim());
  if (categoriaActual) p.set('categoria', categoriaActual);
  if (orden.value !== 'destacados') p.set('orden', orden.value);
  if (soloDisponibles.checked) p.set('disponibles', '1');
  const consulta = p.toString();
  history.replaceState(null, '', consulta ? `?${consulta}` : location.pathname);
}

function pintar() {
  const lista = filtrados();
  vitrina.replaceChildren(...lista.map((lb) => tarjeta(lb, abrirFicha)));
  ponerTexto(
    resultado,
    lista.length
      ? `${plural(lista.length, 'libro')}${lista.length === libros.length ? '' : ` de ${libros.length}`}`
      : 'No encontramos libros con esos filtros. Prueba con otra búsqueda o pregúntale al asistente.',
  );
  guardarEnUrl();
}

function pintarCategorias(lista) {
  const total = lista.reduce((suma, c) => suma + c.libros, 0);
  const botones = [{ categoria: '', libros: total }, ...lista].map((c) => {
    const boton = crear('button', {
      clase: 'chip-categoria',
      texto: `${c.categoria || 'Todas'} (${c.libros})`,
      atributos: { type: 'button', 'aria-pressed': String(c.categoria === categoriaActual) },
      datos: { categoria: c.categoria },
    });
    boton.addEventListener('click', () => {
      categoriaActual = c.categoria;
      cajaCategorias.querySelectorAll('button').forEach((b) => {
        b.setAttribute('aria-pressed', String(b.dataset.categoria === categoriaActual));
      });
      pintar();
    });
    return boton;
  });
  cajaCategorias.replaceChildren(...botones);
}

// ---------------------------------------------------------------------------
// Ficha
// ---------------------------------------------------------------------------

let pedidoFicha = 0;

async function abrirFicha(libro) {
  const pedido = ++pedidoFicha;
  // Mientras llega la ficha completa se muestra lo que ya se sabe del libro.
  fichaCuerpo.replaceChildren(...contenidoFicha({ temas: [], ...libro, edad_min: null, edad_max: null }));
  if (!ficha.open) ficha.showModal();
  try {
    const completa = await fichaLibro(libro.isbn);
    if (pedido === pedidoFicha && ficha.open) fichaCuerpo.replaceChildren(...contenidoFicha(completa));
  } catch {
    /* se queda con el resumen: el precio y la sinopsis ya están */
  }
}

porId('ficha-cerrar').addEventListener('click', () => ficha.close());
ficha.addEventListener('click', (ev) => {
  if (ev.target === ficha) ficha.close(); // clic en el fondo
});

// ---------------------------------------------------------------------------
// Arranque
// ---------------------------------------------------------------------------

function leerUrl() {
  const p = new URLSearchParams(location.search);
  buscar.value = p.get('q') ?? '';
  categoriaActual = p.get('categoria') ?? '';
  if (ORDENES[p.get('orden')]) orden.value = p.get('orden');
  soloDisponibles.checked = p.get('disponibles') === '1';
}

function avisoAcceso() {
  vitrina.replaceChildren();
  resultado.replaceChildren(
    'Esta demo pide iniciar sesión. ',
    crear('a', { clase: 'enlace', texto: 'Entra desde el asistente', atributos: { href: './' } }),
    ' y vuelve a la tienda.',
  );
}

async function cargar() {
  try {
    const acceso = await estadoAcceso().catch(() => null);
    if (acceso?.requerido && !acceso.autenticado) return avisoAcceso();

    const [datosSalud, datos, cats] = await Promise.all([
      salud().catch(() => null),
      catalogoCompleto(),
      categorias().catch(() => []),
    ]);
    if (datosSalud?.tienda) {
      configurarMoneda(datosSalud.tienda.moneda);
      if (datosSalud.tienda.nombre) {
        ponerTexto(porId('marca-nombre'), datosSalud.tienda.nombre);
        document.title = `${datosSalud.tienda.nombre} · Tienda`;
      }
    }
    libros = (datos?.libros ?? []).map((lb) => ({
      ...lb,
      _texto: normalizar(`${lb.titulo} ${lb.autor} ${lb.isbn} ${lb.genero ?? ''}`),
    }));
    pintarCategorias(cats);
    pintar();
  } catch (error) {
    if (error?.requiereAcceso) return avisoAcceso();
    ponerTexto(resultado, `No se pudo cargar el catálogo. ${error?.mensaje ?? ''}`.trim());
  }
}

let temporizador;
buscar.addEventListener('input', () => {
  clearTimeout(temporizador);
  temporizador = setTimeout(pintar, ESPERA_BUSQUEDA_MS);
});
orden.addEventListener('change', pintar);
soloDisponibles.addEventListener('change', pintar);
configurarAvisoDeAcceso(avisoAcceso);

leerUrl();
cargar();
