# 0010 Solo main despliega

Estado: aceptada
Fecha: 2026-11-22

## Contexto

El repositorio tiene colaboradores con permiso de escritura. Pueden crear ramas, subir cambios y lanzar workflows a mano, pero no pueden unir a `dev` ni a `main`.

El despliegue se disparaba con `workflow_run` cuando el CI terminaba en `main`. GitHub ejecuta ese disparador sobre la rama por defecto, que es `dev`. Por eso el environment `produccion`, que entrega las credenciales de Azure, no se podía limitar a `main` sin bloquear el despliegue legítimo, y quedaba abierto a cualquier rama. Un colaborador podía obtener credenciales de Azure de dos formas: lanzando el CD a mano desde su rama, o agregando en su rama un workflow propio que usara ese environment. Así podía desplegar código que el dueño no aprobó.

## Decisión

- El despliegue es el último job del workflow `CI`. Corre solo en un push a `main` y solo si las pruebas y la imagen pasaron, y llama a `.github/workflows/cd.yml` como workflow reutilizable.
- El environment `produccion` admite únicamente la rama `main`. Un job de cualquier otra rama no recibe credenciales, aunque se lance a mano.
- En `main` el CI no cancela una ejecución en curso, para no cortar un despliegue a medias. En las demás ramas sí la cancela.

## Alternativas consideradas

- Mantener `workflow_run` y admitir `main` y `dev` en el environment. Cierra las ramas de trabajo, pero permite desplegar `dev` a mano, y `dev` todavía no es la versión estable.
- Cambiar la rama por defecto a `main`. Permite limitar el environment a `main` sin tocar los workflows, pero los pull requests apuntarían a `main` por defecto y se rompe el flujo de la [decisión 0007](0007-flujo-de-ramas.md).
- Exigir una aprobación manual en el environment. Agrega control, pero cada merge a `main` necesitaría un segundo clic del dueño; queda como opción.

## Consecuencias

- Lo único que llega a Azure es lo que el dueño unió a `main` y pasó las pruebas.
- En los pull requests y en `dev`, el job `Desplegar` aparece como omitido.
- La Container App debe existir antes del primer push a `main`; si no, el job falla.
- Amplía la [decisión 0004](0004-azure-container-apps.md).
