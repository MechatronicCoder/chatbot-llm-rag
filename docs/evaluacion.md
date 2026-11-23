# Evaluación

Las pruebas automáticas comprueban que el código hace lo que se espera con un modelo simulado y un catálogo propio. La evaluación mide otra cosa: cómo se comporta el librero completo, con el modelo, los embeddings y el catálogo reales, frente a mensajes representativos de los clientes.

## Conjunto de casos

`evaluacion/casos.json` tiene 37 casos en 11 categorías:

| Categoría | Casos | Qué se espera |
|---|---|---|
| faq | 7 | Ruta `faq` y la FAQ correcta, también sin tildes; "libros para regalo" sigue siendo FAQ |
| seguridad | 3 | Ruta `filtro`, sin llamar al modelo, incluido un pedido de copia pirata |
| cortesia | 2 | Ruta `directa`, sin herramientas |
| catalogo | 5 | Ruta `herramientas` y el libro esperado, por título (Dune) y por autor (Mary Shelley), también mal escritos ("el silmarilion", "brandon sandersen"); un título que no está en el catálogo no se inventa |
| recomendacion | 8 | Ruta `recomendacion` y uno de los libros esperados entre los mostrados (gatos para un niño de 9 años, misterio en una isla, terror para 13 años, robots detectives, distopía, y un pedido escrito por partes con dos opciones: "comedia o política"); con presupuesto, ningún libro mostrado lo supera; si falta la edad del lector, el librero la pregunta |
| accion | 2 | Ruta `accion` y el pedido en el estado esperado, incluida una conversación de dos turnos (armar el carrito y pagar) |
| carrito | 3 | Ruta `accion` y el carrito con la cantidad de libros esperada: varios libros a la vez, quitar uno y preguntar cuánto falta para el envío gratis |
| rag | 3 | Ruta `rag` y, cuando se indica, el documento esperado entre los citados |
| escalado | 1 | Un cliente que pide hablar con una persona recibe el contacto y el turno queda con ruta `escalar` |
| seguimiento | 1 | Una pregunta que depende de la anterior ("¿cuánto cuesta el envío a Lima?" y luego "¿y a provincias?") la responde el modelo con el historial y menciona la tarifa correcta |
| fuera_de_alcance | 2 | Ruta `directa`, sin inventar catálogo ni políticas |

Cada caso corre en una sesión nueva y sobre una base de datos temporal, así que la evaluación es repetible y no modifica `var/tienda.db`.

Los casos de catálogo, recomendación y acción nombran libros del catálogo actual. `libro_titulo` acepta un título o una lista de títulos válidos, porque una misma petición puede tener varias respuestas correctas (por ejemplo, cualquiera de los libros de La calle del terror para un lector de 13 años). Si el catálogo cambia, se revisan esos títulos y conviene agregar casos para las categorías y edades que más se venden.

## Cómo ejecutarla

```powershell
uv run python scripts/evaluar.py              # con el LLM y los embeddings del .env
uv run python scripts/evaluar.py --sin-llm    # solo los 10 casos deterministas
uv run python scripts/evaluar.py --minimo 0.9 # código de salida 1 si no llega al 90 %
```

El informe se guarda en `evaluacion/resultados/` (excluida de Git) con la tasa de aciertos total y por categoría, cuántas veces el libro esperado estuvo entre los mostrados, la latencia de cada caso y los tokens consumidos.

## Resultado sin modelo

Con el clasificador TF-IDF y las guardias, sin ninguna API configurada:

| Categoría | Aciertos |
|---|---|
| faq | 7/7 |
| seguridad | 3/3 |

Los 10 casos se resuelven en milisegundos y sin tokens. Los 27 casos restantes requieren el modelo y se miden con las claves configuradas.

## Resultado con modelo

Con `qwen-plus` (respaldo `qwen-flash`) y `text-embedding-v4` en la región de Singapur, sobre el catálogo de 200 libros y con el carrito:

| Categoría | Aciertos |
|---|---|
| faq | 7/7 |
| seguridad | 3/3 |
| cortesia | 2/2 |
| catalogo | 5/5 |
| recomendacion | 8/8 |
| accion | 2/2 |
| carrito | 3/3 |
| rag | 3/3 |
| seguimiento | 1/1 |
| escalado | 1/1 |
| fuera_de_alcance | 2/2 |

En total 37/37 rutas y el libro esperado en los 11 casos que lo piden, con una latencia mediana de 3,8 s y unos 195 000 tokens para todo el conjunto. La primera corrida con el carrito dio 30/31 y mostró dos fallos que se corrigieron: el modelo preguntaba "¿deseas agregarlo al carrito?" en lugar de agregarlo, y "¿cuánto me falta para el envío gratis?" lo respondía la FAQ, que no conoce el carrito. El caso del mensaje escrito por partes mostró otro: el modelo recomendaba títulos de memoria, sin llamar al catálogo; ahora la verificación lo detecta y le pide usar recomendar_libros, una búsqueda por cada opción. Con la búsqueda tolerante, la primera corrida dio 33/35: los relacionados que agrega la búsqueda hacían que "quiero 2 de Drácula" ya no contara como un solo libro pedido, y el modelo volvía a preguntar; ahora los relacionados no cuentan. Al pasar las FAQ al mensaje del cliente, la corrida dio 35/37: una FAQ de devoluciones poco parecida desviaba la recomendación de robots detectives, y "quiero hablar con una persona" lo respondía la FAQ en vez de derivar; se corrigieron con `FAQ_PROMPT_MIN` y llevando ese pedido al modelo. Antes de ajustar el prompt y de agregar la verificación de respuestas, el conjunto de 28 casos daba 19/28: el modelo pedía detalles en lugar de recomendar, pedía confirmación en lugar de reservar y respondía políticas sin consultar los documentos.

## Calibrar el umbral del RAG

`RAG_UMBRAL` depende del modelo de embeddings: con un umbral alto, preguntas legítimas sobre las políticas quedan sin contexto; con uno bajo, se cita un fragmento que no responde. Para ajustarlo:

1. Ejecutar la evaluación completa y revisar los casos `rag-*`.
2. En el panel de logs, el evento `rag.recuperado` muestra el mejor puntaje de cada búsqueda.
3. Si una pregunta legítima no trae documentos, bajar el umbral en pasos de 0.05; si aparecen fragmentos irrelevantes, subirlo.
4. Repetir hasta que los casos `rag-*` acierten sin que `fuera-capital` ni `fuera-tarea` recuperen documentos.

El mismo procedimiento aplica a `FAQ_UMBRAL` si se agregan o cambian preguntas frecuentes.

## Calibrar la recomendación

`RECO_UMBRAL` decide qué tan parecido debe ser un libro para ofrecerlo, y los pesos `RECO_PESO_SIMILITUD` y `RECO_PESO_VALORACION` deciden cuánto manda el parecido frente a la opinión de los lectores. Para ajustarlos:

1. Ejecutar la evaluación completa y revisar los casos `reco-*` y la línea "Libro esperado entre los mostrados".
2. En el panel de logs, `recomendacion.generada` muestra los ISBN ofrecidos y `herramienta.ejecutada` la cantidad de recomendaciones.
3. Si una petición razonable no devuelve libros, bajar `RECO_UMBRAL`; si aparecen libros que no tienen relación, subirlo.
4. Si un libro muy bien valorado pero poco relacionado gana al que se pidió, bajar `RECO_PESO_VALORACION`.

Con un catálogo de cientos de libros la calidad depende sobre todo de los datos: una sinopsis de dos o tres líneas y temas concretos mejoran más la recomendación que cualquier ajuste de umbral. `scripts/validar_catalogo.py` avisa qué libros no los tienen.

## Cuando algo falla en modo real

Cada fallo que se ve con el modelo real deja dos rastros, para que no vuelva sin que nadie lo note:

1. Un caso en `casos.json` con el mensaje tal como lo escribió el cliente y un `id` que dice qué pasó (`reco-mensaje-partido`, `carrito-envio-gratis`, `seguimiento-envio`).
2. Una prueba en `tests/` que reproduce la respuesta equivocada con el modelo simulado (`ModeloFalso`) y comprueba que el sistema la corrige. El nombre de la prueba cuenta el caso, como `test_recomendar_de_memoria_se_corrige_con_el_catalogo` o `test_los_relacionados_no_impiden_agregar_el_libro_pedido`.

Recién con los dos en rojo se corrige el prompt, la verificación o el código, y se vuelve a correr la evaluación completa: un arreglo para un caso puede romper otro, como pasó cuando los libros relacionados de la búsqueda hicieron fallar los casos de venta.
