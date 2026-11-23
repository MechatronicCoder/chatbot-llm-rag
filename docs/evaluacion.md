# Evaluación

Las pruebas automáticas comprueban que el código hace lo que se espera con un modelo simulado y un catálogo propio. La evaluación mide otra cosa: cómo se comporta el librero completo, con el modelo, los embeddings y el catálogo reales, frente a mensajes representativos de los clientes.

## Conjunto de casos

`evaluacion/casos.json` tiene 28 casos en 8 categorías:

| Categoría | Casos | Qué se espera |
|---|---|---|
| faq | 7 | Ruta `faq` y la FAQ correcta, también sin tildes; "libros para regalo" sigue siendo FAQ |
| seguridad | 3 | Ruta `filtro`, sin llamar al modelo, incluido un pedido de copia pirata |
| cortesia | 2 | Ruta `directa`, sin herramientas |
| catalogo | 3 | Ruta `herramientas` y el libro esperado, por título (Dune) y por autor (Mary Shelley); un título que no está en el catálogo no se inventa |
| recomendacion | 6 | Ruta `recomendacion` y uno de los libros esperados entre los mostrados (gatos para un niño de 9 años, misterio en una isla, terror para 13 años, robots detectives, distopía); si falta la edad del lector, el librero la pregunta |
| accion | 2 | Ruta `accion`, incluida una conversación de dos turnos (reservar y pagar) |
| rag | 3 | Ruta `rag` y, cuando se indica, el documento esperado entre los citados |
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

Los 10 casos se resuelven en milisegundos y sin tokens. Los 18 casos restantes requieren el modelo y se miden con las claves configuradas.

## Resultado con modelo

Con `qwen-plus` (respaldo `qwen-flash`) y `text-embedding-v4` en la región de Singapur, sobre el catálogo de 180 libros:

| Categoría | Aciertos |
|---|---|
| faq | 7/7 |
| seguridad | 3/3 |
| cortesia | 2/2 |
| catalogo | 3/3 |
| recomendacion | 5/6 |
| accion | 2/2 |
| rag | 3/3 |
| fuera_de_alcance | 2/2 |

En total 27/28 rutas (96 %) y el libro esperado en 7 de 8 casos, con una latencia mediana de 1,7 s y unos 82 000 tokens para todo el conjunto. El caso que varía entre ejecuciones es una recomendación en la que el modelo a veces hace una pregunta antes de recomendar. Antes de ajustar el prompt y de agregar la verificación de respuestas, el mismo conjunto daba 19/28: el modelo pedía detalles en lugar de recomendar, pedía confirmación en lugar de reservar y respondía políticas sin consultar los documentos.

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
