# Registro de decisiones técnicas

Cada documento registra una decisión de arquitectura con el mismo formato: contexto, decisión, alternativas consideradas y consecuencias. Una decisión no se edita cuando cambia: se agrega un documento nuevo que la reemplaza y se marca el anterior como reemplazado.

| N.º | Decisión | Estado |
|---|---|---|
| [0001](0001-un-solo-repositorio.md) | Un solo repositorio para backend, frontend e infraestructura | Aceptada |
| [0002](0002-modelos-por-api.md) | LLM y embeddings por API, sin GPU ni modelos locales | Aceptada |
| [0003](0003-sqlite-con-una-replica.md) | SQLite con una sola réplica | Aceptada |
| [0004](0004-azure-container-apps.md) | Azure Container Apps con Bicep y despliegue por OIDC | Aceptada |
| [0005](0005-frontend-sin-framework.md) | Frontend sin framework ni paso de compilación | Aceptada |
| [0006](0006-guardias-y-faq-antes-del-modelo.md) | Guardias y FAQ antes del modelo | Aceptada |
| [0007](0007-flujo-de-ramas.md) | Flujo de ramas con main y dev, y aprobación del dueño | Aceptada |

Plantilla para una decisión nueva:

```markdown
# NNNN Título en una frase

Estado: propuesta | aceptada | reemplazada por NNNN
Fecha: AAAA-MM-DD

## Contexto
## Decisión
## Alternativas consideradas
## Consecuencias
```
