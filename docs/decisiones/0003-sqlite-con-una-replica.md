# 0003 SQLite con una sola réplica

Estado: aceptada; la carga del catálogo se precisa en [0009](0009-catalogo-como-fuente-de-verdad.md)
Fecha: 2026-11-21

## Contexto

La aplicación guarda catálogo, pedidos, pagos y el historial de cada conversación. La reserva debe descontar el stock y crear el pedido en la misma transacción. En Azure Container Apps el disco del contenedor es temporal y cada réplica tiene el suyo.

## Decisión

Se usa SQLite en el disco del contenedor y la app se despliega con exactamente una réplica (`maxReplicas = 1`). El catálogo se vuelve a cargar desde `datos/catalogo.json` al arrancar con la base vacía.

## Alternativas consideradas

- Azure Database for PostgreSQL. Es la opción correcta para datos persistentes y varias réplicas, pero agrega un servidor administrado con costo fijo, una red que configurar y una migración del acceso a datos. Queda como siguiente paso.
- SQLite sobre un volumen de Azure Files. SQLite no se recomienda sobre sistemas de archivos de red por el manejo de bloqueos.
- Varias réplicas con SQLite. Cada réplica tendría su propio stock y sus propios pedidos: daría resultados distintos según la réplica que atienda.

## Consecuencias

- Pedidos y conversaciones se pierden al reiniciar el contenedor o al desplegar una nueva revisión. Para una demo es aceptable; para producción no.
- Con `minReplicas = 0` se ahorra dinero pero el contenedor se apaga por inactividad y la base vuelve a su estado inicial.
- El límite de peticiones en memoria funciona porque hay una sola réplica.
- El acceso a datos está aislado en `app/persistencia/db.py`, lo que acota el cambio a PostgreSQL a ese módulo.
