# 0007 Flujo de ramas con main y dev, y aprobación del dueño

Estado: aceptada
Fecha: 2026-11-21

## Contexto

El repositorio lo trabajan tres personas. `main` es lo que se despliega en Azure y debe mantenerse estable mientras el producto sigue en desarrollo. Se necesita un lugar donde integrar y probar los cambios del equipo antes de llevarlos a `main`, y evitar que una rama principal se borre o se reescriba por error.

## Decisión

- `main` contiene solo versiones estables y recibe cambios únicamente mediante un pull request desde `dev`.
- `dev` es la rama por defecto y de integración; cada integrante trabaja en una rama `feature/*`, `fix/*` o `docs/*` creada desde `dev` y abre un pull request hacia `dev`.
- Todo pull request debe pasar el CI. La aprobación y la unión, tanto hacia `dev` como de `dev` a `main`, quedan a cargo del dueño del repositorio (`.github/CODEOWNERS`).
- `main` y `dev` no se pueden borrar ni reescribir con `force push`.
- Las reglas se describen como código en `.github/reglas/proteger-main-y-dev.json` (un ruleset de GitHub) y se aplican con un comando.
- Dependabot abre sus pull requests contra `dev`, y el despliegue continuo solo corre desde `main`.

## Alternativas consideradas

- Trabajar todos directamente sobre `main` con ramas cortas. Más simple, pero cada cambio unido llegaría de inmediato a producción.
- Git Flow completo, con ramas `release/*` y `hotfix/*`. Demasiada ceremonia para un equipo pequeño y un único entorno desplegado.

## Consecuencias

- Los cambios se prueban juntos en `dev` antes de llegar a `main`, y `main` siempre corresponde a algo desplegable.
- GitHub solo hace cumplir los rulesets en repositorios públicos o con GitHub Pro. Mientras el repositorio sea privado con cuenta gratuita, las reglas son una convención del equipo y no se aplican automáticamente; al hacerlo público o activar Pro, se activan con el comando documentado en el README.
- Los colaboradores de un repositorio personal tienen siempre permiso de escritura, así que la protección de ramas es la única forma de impedir que alguien borre `main` o `dev`. Borrar el repositorio completo solo lo puede hacer su dueño.
