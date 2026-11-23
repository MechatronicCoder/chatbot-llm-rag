/**
 * agrupador.js — junta los mensajes que el cliente escribe por partes.
 *
 * Mucha gente escribe "hola", "me das un libro", "puede ser de comedia" en
 * mensajes seguidos. Si cada uno fuera un turno, el bot respondería tres veces a
 * medias. El agrupador espera una pausa corta sin escribir y manda todo junto,
 * con un tope desde el primer mensaje para que nadie espere de más.
 */

export const ESPERA_MS = 2000; // pausa sin escribir que cierra el grupo
export const TOPE_MS = 8000; // espera máxima desde el primer mensaje del grupo
export const MAXIMO = 2000; // LIMITE_MENSAJE del servidor

/**
 * @param {object} opciones
 * @param {(texto: string) => void} opciones.alEnviar  recibe el grupo unido con saltos de línea
 */
export function crearAgrupador({ alEnviar, espera = ESPERA_MS, tope = TOPE_MS, maximo = MAXIMO, reloj = Date.now }) {
  let partes = [];
  let inicio = 0;
  let temporizador = null;

  function vaciar() {
    clearTimeout(temporizador);
    temporizador = null;
    if (!partes.length) return;
    const texto = partes.join('\n');
    partes = [];
    alEnviar(texto);
  }

  function programar() {
    clearTimeout(temporizador);
    const restante = tope - (reloj() - inicio);
    temporizador = setTimeout(vaciar, Math.max(0, Math.min(espera, restante)));
  }

  return {
    /** Suma un mensaje al grupo; si con él se pasaría del máximo, primero envía lo anterior. */
    agregar(texto) {
      if (partes.length && [...partes, texto].join('\n').length > maximo) vaciar();
      if (!partes.length) inicio = reloj();
      partes.push(texto);
      programar();
    },
    /** El cliente sigue escribiendo: la pausa vuelve a empezar, sin pasar del tope. */
    escribiendo() {
      if (partes.length) programar();
    },
    /** Envía ya lo que haya. */
    vaciar,
    /** Olvida lo pendiente sin enviarlo (por ejemplo, al cambiar de cliente). */
    descartar() {
      clearTimeout(temporizador);
      temporizador = null;
      partes = [];
    },
  };
}
