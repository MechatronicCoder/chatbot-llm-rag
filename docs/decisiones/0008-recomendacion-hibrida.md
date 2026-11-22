# 0008 Recomendación híbrida dentro del flujo de venta

Estado: aceptada
Fecha: 2026-11-22

## Contexto

El objetivo del chat es vender: entender qué busca el cliente, ofrecerle libros, reservarlos y cobrarlos. Los clientes describen lo que quieren con sus palabras ("un libro para que mi hijo de 5 años aprenda a sumar", "fantasía donde el protagonista sea un goblin") y la librería solo puede ofrecer libros de su catálogo que tengan stock.

Los clientes no califican libros en el chat. El catálogo trae una valoración y una cantidad de votos de una fuente externa. No hay historial de compras: la tienda es nueva y no autentica usuarios. El catálogo previsto ronda los 300 libros.

## Decisión

- El modelo tiene dos herramientas de catálogo: `buscar_libros` cuando el cliente nombra un título, un autor o un ISBN, y `recomendar_libros` cuando describe lo que busca. La segunda define la ruta `recomendacion`.
- El recomendador combina dos señales: `puntaje = 0.75 · similitud / mejor_similitud + 0.25 · valoración_bayesiana / 5`.
- La similitud se calcula con el mismo modelo de embeddings del RAG sobre un texto por libro (título, autor, categoría, género, temas, protagonista, tono y sinopsis), con TF-IDF como respaldo.
- La valoración bayesiana es `(v / (v + m)) · R + (m / (v + m)) · C`, con `m = 20` votos y `C` igual a la media del catálogo. Un libro sin valoración recibe la media.
- Antes de ordenar se aplican filtros duros: libro activo, con stock, dentro de la edad y la categoría pedidas, y no excluido (ya ofrecido o ya leído).
- La venta cruzada usa el campo `similares`, elegido a mano: después de una reserva, el librero puede sugerir una sola vez un similar disponible.
- Las frases de búsqueda ("un libro para", "libros de", "recomiéndame", "parecido a") saltan el clasificador de FAQ. "Quiero un libro para que mi hijo aprenda a sumar" superaba el umbral de la FAQ de devoluciones por compartir "quiero" y "un libro". "Libro de reclamaciones" y "libros para regalo" siguen yendo a la FAQ.
- Sin modelo configurado, el modo demo usa el mismo recomendador y toma la edad del mensaje si aparece ("5 años").

## Alternativas consideradas

- Filtrado colaborativo (usuario-ítem o factorización de matrices). Necesita muchas interacciones de muchos clientes y sufre el problema de arranque en frío; la tienda no tiene historial ni usuarios autenticados.
- Ordenar solo por valoración. Ignora lo que pidió el cliente.
- Promedio simple de la valoración. Un 5.0 con un voto supera a un 4.8 con 600.
- Poner el catálogo completo en el prompt y dejar que el modelo elija. Con 300 libros cuesta miles de tokens por mensaje y el modelo puede mezclar o inventar datos.
- Búsqueda solo por palabras. "Aprender a sumar" no encuentra un libro que dice "matemáticas" o "números".
- Permitir que los clientes califiquen en el chat. No forma parte del flujo de venta y, sin autenticación, sería fácil de manipular.

## Consecuencias

- Solo se ofrecen libros que existen y tienen stock. Precio, stock y estado se leen de la base en cada recomendación, así que un libro agotado por una reserva deja de ofrecerse al instante.
- La calidad depende de los datos: una sinopsis breve y temas concretos importan más que los parámetros. `scripts/validar_catalogo.py` avisa qué libros no los tienen.
- Indexar el catálogo cuesta una llamada de embeddings por cada 10 libros al arrancar, y los vectores quedan en `var/embeddings_catalogo.npz`. Cada recomendación agrega una llamada para el texto del cliente.
- Los pesos, el umbral y el mínimo de votos son variables `RECO_*` y se calibran con `scripts/evaluar.py`.
- Si más adelante hay historial de compras, una señal colaborativa se puede sumar al puntaje sin cambiar la herramienta que ve el modelo.
- Amplía la [decisión 0006](0006-guardias-y-faq-antes-del-modelo.md) (una guardia más antes del clasificador) y la [decisión 0002](0002-modelos-por-api.md) (el catálogo también se indexa con embeddings por API).
