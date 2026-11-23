# 0013 El cliente paga con un botón y la tienda guarda solo marca y 4 dígitos

Estado: aceptada
Fecha: 2026-11-23

## Contexto

En la demo publicada, un cliente escribió "con transferencia bancaria" y el bot cobró el pedido en ese mismo instante y prometió plazos de entrega que no estaban en las políticas. El modelo tenía la herramienta `cobrar_pedido`: bastaba con que interpretara un mensaje como un pedido de pago para que el dinero se moviera. Un compañero lo resumió así: el bot no debería poder usar la tarjeta.

También se pidió que una tarjeta que el cliente escribe quede guardada, para elegirla después al pagar.

## Decisión

- El modelo ya no tiene `cobrar_pedido`. Puede armar el carrito, reservar, consultar y cancelar, pero no cobrar.
- El cobro lo confirma el cliente con el botón «Pagar» de la tarjeta del pedido, después de ver el total calculado por el sistema (`POST /api/pedidos/{pedido_id}/pago`). En la demo el pago es simulado.
- Al pagar se elige una tarjeta guardada, "Otra tarjeta nueva", efectivo contra entrega o transferencia.
- De una tarjeta solo se guarda la marca y los últimos cuatro dígitos ("Visa •••• 4242"), como hacen las tiendas que cobran con una pasarela. El número completo se valida con el dígito verificador y se descarta; la fecha y el CVV no se piden.
- Si el cliente escribe la tarjeta en el chat, se guarda igual y el número se reemplaza por una marca antes de llegar a la base, al log o al modelo, que solo recibe una nota con la marca y los 4 dígitos.
- Cuando el cliente quiere pagar, la respuesta trae la tarjeta de su carrito o de su último pedido reservado, con el botón a la vista.
- La verificación rechaza que el modelo diga "pagado" si la base no lo confirma, y que prometa plazos, comprobantes o guías sin haber consultado las políticas.

## Alternativas consideradas

- Mantener `cobrar_pedido` con una confirmación por texto ("¿confirmas el pago?"). Sigue dependiendo de que el modelo interprete bien un "sí" y no evita que cobre por su cuenta.
- Guardar el número completo cifrado. Haría a la tienda responsable de datos de tarjetas sin necesitarlos: para mostrar y elegir una tarjeta bastan la marca y los 4 dígitos, y el cobro real lo haría la pasarela con su propio token.
- Pedir la tarjeta por el chat. Deja el número en la conversación y depende del modelo para tratarlo.

## Consecuencias

- Ningún mensaje, por ambiguo que sea, puede mover dinero: siempre hace falta el clic del cliente.
- Pagar toma un paso más que antes, pero el cliente ve el total y el medio de pago antes de confirmar.
- La base pasa a la versión 5 del esquema (tabla `tarjetas` y la tarjeta usada en cada pago); como guarda datos de demostración, se reconstruye al arrancar.
