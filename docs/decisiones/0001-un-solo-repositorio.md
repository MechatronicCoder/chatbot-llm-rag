# 0001 Un solo repositorio para backend, frontend e infraestructura

Estado: aceptada
Fecha: 2026-11-21

## Contexto

El producto es una sola pantalla de chat (HTML, CSS y JavaScript) servida por la misma API FastAPI que la consume. Lo desarrolla un equipo de tres personas, se despliega como una única imagen y el enunciado exige que cualquier persona pueda clonar, instalar y ejecutar el proyecto sin asistencia.

## Decisión

Backend, frontend, infraestructura y documentación viven en un solo repositorio, con carpetas separadas por responsabilidad (`app/`, `frontend/`, `infra/`, `docs/`).

## Alternativas consideradas

- Un repositorio para el backend y otro para el frontend. Tiene sentido cuando cada parte la mantiene un equipo distinto, se publica a otro ritmo o el frontend es una aplicación propia con su compilación y su despliegue. Aquí duplicaría la configuración de CI, obligaría a versionar el contrato de la API entre repositorios y haría que el evaluador clone y conecte dos proyectos.
- Un repositorio adicional solo para la infraestructura. Útil cuando varios servicios comparten la misma plataforma; con un único servicio solo agrega coordinación.

## Consecuencias

- Un cambio que toca la API y la pantalla queda en un solo commit y un solo pull request.
- Una sola imagen Docker y un solo pipeline de CI/CD.
- Si el frontend creciera hasta necesitar un framework con compilación, se podría separar sin cambiar la API, porque el contrato ya está documentado en `docs/api.md`.
