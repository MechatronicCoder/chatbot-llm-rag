# 0009 El archivo del catálogo como fuente de verdad

Estado: aceptada
Fecha: 2026-11-22

## Contexto

El catálogo de libros (unos 300) se mantiene a mano en `datos/catalogo.json`, por lo que tendrá errores de tipeo: un ISBN con un dígito de más, una edad mínima mayor que la máxima o un libro similar que ya no existe. A la vez, la base guarda el stock, que las reservas descuentan.

Antes, el catálogo se cargaba solo cuando la base estaba vacía: un cambio de precio o de sinopsis en el archivo no se veía hasta borrar la base. Además, el esquema cambió de productos a libros, y una base creada con el esquema anterior fallaría al arrancar.

## Decisión

- Al arrancar, cada libro se valida con un modelo pydantic (`app/catalogo/modelo.py`):
  - ISBN-13 con dígito verificador, sin repetidos;
  - rango de edad coherente;
  - valoración siempre acompañada de su cantidad de votos;
  - `similares` que existan en el catálogo y no apunten al mismo libro;
  - precio mayor que cero, redondeado a dos decimales;
  - portada como ruta local.

  Se informan todos los problemas a la vez, con el libro, el ISBN y el campo. Con un solo error la app no arranca y la base no se toca.
- Después de validar, el catálogo se sincroniza con la base en una sola transacción:
  - un libro nuevo se inserta con el stock del archivo;
  - un libro existente actualiza sus datos y su precio, pero conserva el stock de la base;
  - un libro que ya no está en el archivo queda inactivo (`activo = 0`) y sus pedidos se conservan;
  - un libro inactivo que vuelve al archivo se reactiva.
- La base guarda su versión en `pragma user_version`. Si no coincide con `ESQUEMA_VERSION`, las tablas se reconstruyen.
- La misma validación corre en `scripts/validar_catalogo.py`, en el CI y en un hook de pre-commit que se activa al cambiar el archivo.
- El nombre de la tienda y la moneda están en `datos/tienda.json`, validado de la misma forma.

## Alternativas consideradas

- Cargar el catálogo solo con la base vacía (el comportamiento anterior). Los cambios del archivo se ignoraban sin aviso.
- Sobrescribir todo, incluido el stock, en cada arranque. Un reinicio devolvería los ejemplares ya reservados o vendidos.
- Borrar los libros que salen del archivo. Rompería los pedidos que los referencian.
- Una herramienta de migraciones (Alembic). Es excesiva para una base SQLite con datos de demostración; corresponde cuando haya datos que conservar (ver la [decisión 0003](0003-sqlite-con-una-replica.md)).
- Un panel de administración para editar el catálogo. Fuera de alcance: el archivo está versionado en Git y cada cambio pasa por un pull request con CI.

## Consecuencias

- Cambiar un precio o una sinopsis es editar el archivo y reiniciar la app; el log `catalogo.sincronizado` informa cuántos libros son nuevos, cuántos se actualizaron y cuántos se retiraron.
- Para reponer el stock desde el archivo se borra `var/tienda.db` (o el volumen con `docker compose down -v`).
- Un catálogo inválido no llega a `dev` ni a `main`, porque el CI falla.
- Subir `ESQUEMA_VERSION` borra pedidos y conversaciones locales. Es aceptable mientras la base tenga datos de demostración; con datos reales se necesitarán migraciones.
- Precisa la carga del catálogo descrita en la [decisión 0003](0003-sqlite-con-una-replica.md).
