# 0012 Carrito con montos calculados por el sistema

Estado: aceptada
Fecha: 2026-11-23

## Contexto

Hasta ahora un pedido tenía un solo libro y el envío no se calculaba en ningún lado: solo estaba escrito en las políticas. Cuando el cliente pedía varios libros o preguntaba cuánto le salía con envío, el modelo hacía la suma. Un modelo de lenguaje suma mal, redondea a su manera y puede inventar un total que nadie cobra.

Además, el dinero se guardaba como número de coma flotante (`real`), que acumula errores de céntimos al sumar.

## Decisión

- Un pedido nace como carrito, uno abierto por cliente, y pasa a reservado, pagado o cancelado. Sus libros van en `pedido_lineas`.
- Los montos se guardan en céntimos enteros. Las cuentas se hacen en `app/catalogo/cotizacion.py`, con funciones puras.
- Las tarifas de envío viven en `datos/tienda.json`: Lima S/ 10, gratis desde S/ 120; provincias S/ 18, gratis desde S/ 200. Una prueba comprueba que el esquema de la herramienta ofrece las mismas zonas.
- La herramienta `actualizar_carrito` fija la cantidad final de cada libro y la zona, vuelve a leer los precios y devuelve el carrito con un resumen ya redactado. `reservar_pedido` reserva el carrito entero y descuenta el stock de todas las líneas en una transacción.
- Confirmación fija: `cobrar_pedido` se rechaza si el total de ese pedido cambió en el mismo turno, porque el cliente todavía no lo vio. Un carrito que el cliente ya vio se reserva y cobra en un solo paso. Si un precio cambió desde entonces, no se cobra.
- La verificación de respuestas rechaza cualquier monto en soles que no sea un precio del catálogo, una tarifa, un monto de un pedido del cliente o un número que el propio cliente escribió. Si el modelo insiste, el cliente recibe el resumen del sistema.
- La base pasa a la versión 3 del esquema; como guarda datos de demostración, se reconstruye al arrancar.

## Alternativas consideradas

- Mantener un libro por pedido y crear varios pedidos. Cobraría el envío varias veces y no resuelve la suma.
- Calcular el total en el frontend. El modelo igual escribiría montos en su respuesta, sin control.
- Guardar `Decimal` como texto. Funciona, pero los enteros se suman y comparan directamente en SQL.
- Pedir confirmación con una herramienta aparte. Agrega una vuelta del modelo; comparar el turno en que cambió el total logra lo mismo sin otra herramienta.

## Consecuencias

- El cliente siempre ve el total calculado por el sistema antes de pagar. Si pide pagar en el mismo mensaje en que arma el carrito, primero recibe el resumen y confirma después.
- El carrito no aparta stock: otro cliente puede llevarse el último ejemplar antes de la reserva, que entonces falla con el stock real.
- Cambiar una tarifa es editar `tienda.json` y la zona en el esquema de la herramienta, si se agrega una nueva.
- La API de pedidos cambió de forma: cada pedido trae `lineas`, `subtotal`, `envio` y `total`, y el CSV de ventas tiene una fila por libro.
