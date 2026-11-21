# 0002 LLM y embeddings por API, sin GPU ni modelos locales

Estado: aceptada
Fecha: 2026-11-21

## Contexto

El asistente necesita un modelo de lenguaje con function calling y un modelo de embeddings para el RAG. Una alternativa habitual para los embeddings es ejecutar un modelo local con sentence-transformers, que depende de PyTorch. Eso agrega varios gigabytes a la imagen, exige más memoria en el servidor y alarga el arranque, porque el modelo se descarga y se carga en cada inicio.

## Decisión

- El LLM se consume por API compatible con OpenAI: DeepSeek como principal y Qwen como respaldo. Si el principal falla (clave inválida, cuota, error del servidor o sin red), el cliente pasa al siguiente.
- Los embeddings se consumen por API: Qwen `text-embedding-v4` en Alibaba Cloud Model Studio, en lotes de 10 textos.
- Los vectores de los documentos se guardan en caché en disco y solo se recalculan si cambian los textos o el modelo.
- Siempre se construye también un índice TF-IDF: si la API de embeddings falla, la búsqueda sigue funcionando con menor calidad.

## Alternativas consideradas

- Embeddings locales con sentence-transformers y PyTorch. Sin costo por llamada, pero con una imagen mucho más pesada, más memoria y un arranque lento. En la versión de PyTorch para Linux, además, se descargan por defecto las bibliotecas de GPU.
- Embeddings de DeepSeek. Su documentación oficial no publica un endpoint de embeddings, por lo que no se usó.
- Un servidor de inferencia propio con GPU. Fuera de alcance por costo y operación.

## Consecuencias

- La imagen queda en cientos de megabytes y corre con 0.5 vCPU y 1 GiB.
- Indexar los PDFs cuesta centavos; cada pregunta al RAG agrega una llamada de embeddings.
- El servicio depende de dos proveedores externos. Ambos se pueden reemplazar por cualquier otro compatible con OpenAI cambiando solo variables de entorno.
- El umbral `RAG_UMBRAL` depende del modelo de embeddings y se calibra con `scripts/evaluar.py`.
