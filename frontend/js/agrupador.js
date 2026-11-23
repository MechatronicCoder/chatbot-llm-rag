/**
 * agrupador.js — junta los mensajes que el cliente escribe por partes.
 *
 * Mucha gente escribe "hola", "me das un libro", "puede ser de comedia" en
 * mensajes seguidos. Si cada uno fuera un turno, el bot respondería tres veces a
 * medias. El agrupador espera una pausa sin escribir y manda todo junto.
 *
 * La pausa depende de cómo termina lo último que se escribió:
 * - "¿tienen Dune?", "gracias." o "¡genial!" parecen completos: se espera poco;
 * - "hola", "no sé", "algo de ciencia" o "puede ser de" suelen seguir: se espera
 *   más, porque quien escribe a veces se detiene a pensar a mitad de la idea.
 * Mientras se escribe en la caja, el grupo sigue abierto. Un tope desde el primer
 * mensaje evita que alguien espere de más.
 */

export const ESPERA_CORTA_MS = 1500; // lo último parece completo
export const ESPERA_LARGA_MS = 4500; // lo último parece seguir
export const TOPE_MS = 15000; // espera máxima desde el primer mensaje del grupo
export const MAXIMO = 2000; // LIMITE_MENSAJE del servidor

/** Termina en signo de cierre ("?", "!", ".", "…") o en un emoji: parece completo. */
const COMPLETO = /(?:[?!.…]|\p{Extended_Pictographic})\s*$/u;

/** Cuánto esperar después de un mensaje, según cómo termina. */
export function esperaPara(texto) {
  return COMPLETO.test(texto) ? ESPERA_CORTA_MS : ESPERA_LARGA_MS;
}

/**
 * @param {object} opciones
 * @param {(texto: string) => void} opciones.alEnviar  recibe el grupo unido con saltos de línea
 * @param {(pendiente: boolean) => void} [opciones.alCambiar]  avisa si hay un grupo esperando
 */
export function crearAgrupador({ alEnviar, alCambiar, tope = TOPE_MS, maximo = MAXIMO, reloj = Date.now }) {
  let partes = [];
  let inicio = 0;
  let temporizador = null;

  function vaciar() {
    clearTimeout(temporizador);
    temporizador = null;
    if (!partes.length) return;
    const texto = partes.join('\n');
    partes = [];
    alCambiar?.(false);
    alEnviar(texto);
  }

  function programar(espera) {
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
      alCambiar?.(true);
      programar(esperaPara(texto));
    },
    /** El cliente sigue escribiendo: el grupo queda abierto, sin pasar del tope. */
    escribiendo() {
      if (partes.length) programar(ESPERA_LARGA_MS);
    },
    /** Envía ya lo que haya. */
    vaciar,
    /** Olvida lo pendiente sin enviarlo (por ejemplo, al cambiar de cliente). */
    descartar() {
      clearTimeout(temporizador);
      temporizador = null;
      partes = [];
      alCambiar?.(false);
    },
  };
}
