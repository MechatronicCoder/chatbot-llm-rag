# 0011 Acceso de demo con usuario y clave

Estado: aceptada
Fecha: 2026-11-22

## Contexto

La app se publica en una URL de Azure para la presentación. Cada mensaje del chat puede costar llamadas a los modelos, y hasta ahora cualquiera con la URL, incluido un bot, podía usarlo. El límite de mensajes por IP y el tope de tokens acotan el gasto, pero no impiden el uso.

El repositorio es público, así que ninguna credencial puede quedar en el código. Solo hace falta un acceso compartido para el equipo y el profesor, no cuentas por cliente.

## Decisión

- Un único usuario y una clave, configurados con `ACCESO_USUARIO` y `ACCESO_CLAVE_HASH`.
  - La clave solo existe como hash PBKDF2-SHA256 de 600 000 iteraciones, con sal aleatoria.
  - En local el hash se genera con `scripts/hash_clave.py`. En Azure se calcula en el script de la fase 2 y se guarda como secreto de Container Apps.
- Con esas variables definidas, toda la API pide sesión salvo `/api/health*`, que usan las sondas de Azure, y `/api/acceso/*`. En producción la app no arranca sin ellas.
- La sesión es un token aleatorio de 256 bits en una cookie HttpOnly, SameSite=Strict y Secure en producción. Dura 8 horas y vive en memoria, igual que el límite de peticiones, porque hay una sola réplica.
- Contra bots, los intentos se cuentan antes de calcular el hash: 5 por IP cada 15 minutos y 100 en total en el mismo período.
- El usuario y la clave se verifican siempre los dos, en tiempo constante. El error no dice cuál falló, y el log registra el intento sin la clave.
- El frontend muestra una pantalla de acceso que bloquea la página, y vuelve a aparecer si la sesión vence.

## Alternativas consideradas

- Autenticación integrada de Azure Container Apps con Microsoft o GitHub. No toca el código, pero cada persona necesita una cuenta en una lista de permitidos, incluido el profesor.
- La clave en texto como variable de entorno. Es más simple, pero una filtración del entorno o de un log la expondría tal cual.
- Un token firmado (JWT) en lugar de sesiones en memoria. No hace falta con una sola réplica y agrega una clave de firma más que administrar.
- Restringir el acceso por IP en Container Apps. No sirve para una presentación desde una red que no se conoce de antemano.

## Consecuencias

- Sin la clave nadie llega al chat, así que el gasto de la API queda en manos del equipo.
- Un reinicio del contenedor cierra todas las sesiones y hay que volver a iniciar sesión.
- Si un bot agota el tope total de intentos, durante 15 minutos nadie puede iniciar sesión, aunque las sesiones abiertas siguen funcionando. Conviene iniciar sesión antes de presentar.
- Cambiar la clave es volver a calcular el hash y actualizar el secreto, sin desplegar código.
