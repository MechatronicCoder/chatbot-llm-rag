# Registro de decisiones técnicas

Cada documento registra una decisión de arquitectura con el mismo formato: contexto, decisión, alternativas consideradas y consecuencias. Una decisión no se edita cuando cambia: se agrega un documento nuevo que la reemplaza o la amplía, y en el anterior solo se anota esa relación en el estado.

| N.º | Decisión | Estado |
|---|---|---|
| [0001](0001-un-solo-repositorio.md) | Un solo repositorio para backend, frontend e infraestructura | Aceptada |
| [0002](0002-modelos-por-api.md) | LLM y embeddings por API, sin GPU ni modelos locales | Aceptada |
| [0003](0003-sqlite-con-una-replica.md) | SQLite con una sola réplica | Aceptada |
| [0004](0004-azure-container-apps.md) | Azure Container Apps con Bicep y despliegue por OIDC | Aceptada |
| [0005](0005-frontend-sin-framework.md) | Frontend sin framework ni paso de compilación | Aceptada |
| [0006](0006-guardias-y-faq-antes-del-modelo.md) | Guardias y FAQ antes del modelo | Aceptada |
| [0007](0007-flujo-de-ramas.md) | Flujo de ramas con main y dev, y aprobación del dueño | Aceptada |
| [0008](0008-recomendacion-hibrida.md) | Recomendación híbrida (contenido y valoración) dentro del flujo de venta | Aceptada |
| [0009](0009-catalogo-como-fuente-de-verdad.md) | El archivo del catálogo como fuente de verdad; la base, dueña del stock | Aceptada |
| [0010](0010-solo-main-despliega.md) | Solo main despliega: el despliegue es un job del CI y el environment acepta solo main | Aceptada |
| [0011](0011-acceso-de-demo.md) | Acceso de demo con usuario y clave, sesión en cookie y límites contra bots | Aceptada |
| [0012](0012-carrito-y-montos-en-centimos.md) | Carrito con subtotal, envío y total calculados por el sistema, en céntimos | Aceptada |
| [0013](0013-el-cliente-paga-con-un-boton.md) | El cliente paga con un botón; de las tarjetas solo se guardan marca y 4 dígitos | Aceptada |

Plantilla para una decisión nueva:

```markdown
# NNNN Título en una frase

Estado: propuesta | aceptada | ampliada por NNNN | reemplazada por NNNN
Fecha: AAAA-MM-DD

## Contexto
## Decisión
## Alternativas consideradas
## Consecuencias
```
