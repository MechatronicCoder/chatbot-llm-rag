# 0005 Frontend sin framework ni paso de compilación

Estado: aceptada
Fecha: 2026-11-21

## Contexto

La interfaz es una sola pantalla: el chat, un panel con el cliente, sus pedidos y el catálogo, y un panel de logs en vivo. Necesita animaciones sutiles, accesibilidad y una política de seguridad de contenido estricta.

## Decisión

El frontend se escribe en HTML, CSS y JavaScript con módulos ES nativos, sin framework ni compilación. FastAPI lo sirve como archivos estáticos desde `frontend/`. Cada módulo tiene una responsabilidad: cliente HTTP, chat, panel lateral, logs y utilidades.

## Alternativas consideradas

- React, Vue o Svelte con Vite. Aportan componentes y estado reactivo, pero agregan Node.js, un paso de compilación en el Dockerfile y en el CI, y una carpeta de dependencias para una sola pantalla.
- Streamlit o Gradio. Rápidos para prototipos en Python, pero limitan el diseño, las animaciones y el control sobre las cabeceras de seguridad.

## Consecuencias

- Un cambio en el frontend se ve recargando el navegador, sin compilar.
- La Content-Security-Policy puede prohibir scripts y estilos en línea, lo que reduce el riesgo de inyección de código.
- Si la interfaz creciera a varias vistas con estado compartido, convendría migrar a un framework; el contrato de la API no cambiaría.
