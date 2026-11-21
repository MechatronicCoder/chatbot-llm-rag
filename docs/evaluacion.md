# Evaluación

Las pruebas automáticas comprueban que el código hace lo que se espera con un modelo simulado. La evaluación mide otra cosa: cómo se comporta el asistente completo, con el modelo y los embeddings reales, frente a mensajes representativos de los clientes.

## Conjunto de casos

`evaluacion/casos.json` tiene 23 casos en 7 categorías:

| Categoría | Casos | Qué se espera |
|---|---|---|
| faq | 6 | Ruta `faq` y la FAQ correcta, también sin tildes |
| seguridad | 3 | Ruta `filtro`, sin llamar al modelo |
| cortesia | 2 | Ruta `directa`, sin herramientas |
| catalogo | 4 | Ruta `herramientas`, incluido un producto que no existe |
| accion | 2 | Ruta `accion`, incluida una conversación de dos turnos (reservar y pagar) |
| rag | 4 | Ruta `rag` y, cuando se indica, el documento esperado entre los citados |
| fuera_de_alcance | 2 | Ruta `directa`, sin inventar catálogo ni políticas |

Cada caso corre en una sesión nueva y sobre una base de datos temporal, así que la evaluación es repetible y no modifica `var/tienda.db`.

## Cómo ejecutarla

```powershell
uv run python scripts/evaluar.py              # con el LLM y los embeddings del .env
uv run python scripts/evaluar.py --sin-llm    # solo los 9 casos deterministas
uv run python scripts/evaluar.py --minimo 0.9 # código de salida 1 si no llega al 90 %
```

El informe se guarda en `evaluacion/resultados/` (excluida de Git) con la tasa de aciertos total y por categoría, la latencia de cada caso y los tokens consumidos.

## Resultado sin modelo

Con el clasificador TF-IDF y las guardias, sin ninguna API configurada:

| Categoría | Aciertos |
|---|---|
| faq | 6/6 |
| seguridad | 3/3 |

Los 9 casos se resuelven en menos de 10 ms cada uno y sin tokens. Los 14 casos restantes requieren el modelo y se miden con las claves configuradas.

## Calibrar el umbral del RAG

`RAG_UMBRAL` depende del modelo de embeddings: con un umbral alto, preguntas legítimas sobre los manuales quedan sin contexto; con uno bajo, se cita un fragmento que no responde. Para ajustarlo:

1. Ejecutar la evaluación completa y revisar los casos `rag-*`.
2. En el panel de logs, el evento `rag.recuperado` muestra el mejor puntaje de cada búsqueda.
3. Si una pregunta legítima no trae documentos, bajar el umbral en pasos de 0.05; si aparecen fragmentos irrelevantes, subirlo.
4. Repetir hasta que los casos `rag-*` acierten sin que `fuera-capital` ni `fuera-tarea` recuperen documentos.

El mismo procedimiento aplica a `FAQ_UMBRAL` si se agregan o cambian preguntas frecuentes.
