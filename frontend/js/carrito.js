/**
 * carrito.js — tarjeta del carrito o del pedido que tocó una respuesta del chat.
 *
 * Los montos (importe de cada línea, subtotal, envío y total) llegan calculados
 * por el servidor: aquí solo se formatean, nunca se suman. Todo el contenido se
 * inserta como texto, nunca como HTML.
 *
 * El pago lo hace el cliente con el botón «Pagar» de la tarjeta, después de ver el
 * total: el asistente no puede cobrar. En la demo el pago es simulado.
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

const NUEVA = 'nueva';

/** Opciones de pago: tarjetas guardadas (solo marca y 4 dígitos), una nueva, efectivo o transferencia. */
function opcionesDePago(cargarTarjetas) {
  const metodo = crear('select', { clase: 'carrito-metodo', atributos: { 'aria-label': 'Cómo pagar' } }, [
    crear('option', { texto: 'Otra tarjeta nueva', atributos: { value: NUEVA } }),
    crear('option', { texto: 'Efectivo contra entrega', atributos: { value: 'efectivo' } }),
    crear('option', { texto: 'Transferencia bancaria', atributos: { value: 'transferencia' } }),
  ]);
  cargarTarjetas?.()
    .then((tarjetas) => {
      const guardadas = tarjetas.map((t) =>
        crear('option', { texto: `Tarjeta ${t.etiqueta}`, atributos: { value: `tarjeta:${t.id}` } }),
      );
      metodo.prepend(...guardadas);
      if (guardadas.length) metodo.value = guardadas[0].value;
      metodo.dispatchEvent(new Event('change'));
    })
    .catch(() => {}); // sin tarjetas guardadas, quedan las demás opciones
  return metodo;
}

/** Lo elegido en el selector, en la forma que espera alPagar. */
function elegido(valor, numero) {
  if (valor === NUEVA) return { metodo: 'tarjeta', numeroNuevo: numero.value.trim() };
  if (valor.startsWith('tarjeta:')) return { metodo: 'tarjeta', tarjetaId: valor.slice('tarjeta:'.length) };
  return { metodo: valor };
}

/** Cómo pagar y botón «Pagar». Al pagar, la tarjeta se reemplaza por la del pedido pagado. */
function filaPago(c, { alPagar, cargarTarjetas }, obtenerTarjeta) {
  const sinZona = c.envio === null || c.envio === undefined;
  const metodo = opcionesDePago(cargarTarjetas);
  const numero = crear('input', {
    clase: 'carrito-numero',
    atributos: {
      type: 'text', inputmode: 'numeric', autocomplete: 'cc-number', maxlength: '23',
      placeholder: 'Número de la tarjeta', 'aria-label': 'Número de la tarjeta nueva',
    },
  }); // prettier-ignore
  const boton = crear('button', {
    clase: 'carrito-pagar',
    texto: `Pagar ${formatearPrecio(c.total)}`,
    atributos: { type: 'button' },
  });
  const aviso = crear('p', {
    clase: 'carrito-aviso',
    texto: sinZona
      ? 'Para pagar falta la zona de envío: dile al asistente si es Lima o provincias.'
      : 'Pago simulado de la demo: de la tarjeta solo se guardan la marca y los últimos 4 dígitos.',
  });
  const bloquear = (si) => {
    for (const control of [metodo, numero, boton]) control.disabled = si || sinZona;
  };
  metodo.addEventListener('change', () => {
    numero.hidden = metodo.value !== NUEVA;
  });
  boton.addEventListener('click', async () => {
    bloquear(true);
    aviso.textContent = 'Registrando el pago…';
    try {
      const r = await alPagar(c.pedido_id, elegido(metodo.value, numero));
      numero.value = '';
      const pagada = tarjetaCarrito(r.pedido);
      const medio = r.tarjeta ?? r.metodo;
      pagada?.append(crear('p', { clase: 'carrito-nota', texto: `Pago ${r.pago_id} registrado con ${medio}.` }));
      if (pagada) obtenerTarjeta().replaceWith(pagada);
    } catch (error) {
      aviso.textContent = error?.mensaje || 'No se pudo registrar el pago. Inténtalo de nuevo.';
      bloquear(false);
    }
  });
  bloquear(false);
  return crear('div', { clase: 'carrito-pago' }, [metodo, numero, boton, aviso]);
}

/** Tarjeta del pedido, o null si el turno no tocó ninguno. Con alPagar, muestra el botón «Pagar». */
export function tarjetaCarrito(c, { alPagar, cargarTarjetas } = {}) {
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
  let tarjeta = null;
  if (alPagar && (c.estado === 'carrito' || c.estado === 'reservado')) {
    hijos.push(filaPago(c, { alPagar, cargarTarjetas }, () => tarjeta));
  }
  tarjeta = crear('section', { clase: 'carrito', atributos: { 'aria-label': `Pedido ${c.pedido_id ?? ''}, ${estado}` } }, hijos);
  return tarjeta;
}
