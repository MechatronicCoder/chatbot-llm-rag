# 0006 Guardias y FAQ antes del modelo

Estado: aceptada
Fecha: 2026-11-21

## Contexto

Enviar cada mensaje directamente al LLM tiene tres problemas: cuesta tokens incluso para preguntas que tienen una respuesta fija, el modelo puede improvisar sobre temas sensibles (pagos, plazos, reclamos) y un mensaje malicioso llega intacto a las instrucciones del sistema.

## Decisión

Cada turno sigue un orden fijo:

1. Redacción de datos sensibles (tarjeta, CVV, claves) antes de cualquier otro paso.
2. Guardias de inyección de instrucciones y de temas fuera de política.
3. Clasificador TF-IDF de preguntas frecuentes: con similitud de al menos `FAQ_UMBRAL` y un margen claro sobre la segunda opción, se responde la FAQ sin llamar al modelo. Si el tema es crítico y no hay ninguna FAQ parecida, se deriva a una persona.
4. Solo entonces el LLM, con herramientas y con las FAQs cercanas como contexto verificado.

Las peticiones de acción sobre un pedido ("págala", "cancélalo", un identificador `PED-…`) saltan el clasificador, porque dependen del historial que solo ve el modelo.

## Alternativas consideradas

- Dejar que el modelo decida todo, con las FAQs en el prompt. Más simple, pero más caro, más lento y menos predecible.
- Un clasificador de intenciones entrenado (por ejemplo con Rasa). Más preciso con muchos datos, pero requiere un conjunto de entrenamiento que el caso no tiene; TF-IDF de palabras y caracteres funciona bien con 10 FAQs y sus variaciones.

## Consecuencias

- Las preguntas frecuentes se responden en milisegundos y sin costo.
- El comportamiento de los casos frecuentes y sensibles es determinista y se puede probar sin red.
- Los umbrales (`FAQ_UMBRAL`, `FAQ_MARGEN`, `FAQ_CONTEXTO_MIN`) deben ajustarse si cambian las FAQs; el conjunto de evaluación lo detecta.
