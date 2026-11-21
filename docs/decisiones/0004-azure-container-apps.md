# 0004 Azure Container Apps con Bicep y despliegue por OIDC

Estado: aceptada
Fecha: 2026-11-21

## Contexto

El proyecto debe quedar desplegado y accesible desde internet. La aplicación ya se distribuye como imagen Docker, no necesita GPU y debe servir HTTPS. El equipo cuenta con una suscripción de Azure.

## Decisión

- Se despliega en Azure Container Apps (plan Consumption), con la imagen en Azure Container Registry y los logs en Log Analytics.
- La infraestructura se describe en Bicep (`infra/main.bicep`) y se despliega en dos fases: primero registro, identidad y entorno; después la app con su imagen.
- La app descarga la imagen con una identidad administrada con rol AcrPull, sin usuario ni contraseña del registro.
- Las claves de los modelos y el token de administración son secretos de Container Apps.
- GitHub Actions despliega con OIDC: Azure confía en los tokens que GitHub emite para este repositorio y entorno, sin guardar contraseñas en GitHub.
- La imagen se construye en el runner de GitHub y se sube con `docker push`, sin usar ACR Tasks, que puede no estar disponible en suscripciones de prueba.

## Alternativas consideradas

- Azure App Service. También ejecuta contenedores, pero Container Apps escala a cero, cobra por uso y tiene revisiones con sondas de salud integradas.
- Azure Kubernetes Service. Desproporcionado para un solo contenedor.
- Configurar los recursos a mano en el portal. Rápido la primera vez, pero no reproducible ni revisable.
- Credenciales de un service principal guardadas como secreto en GitHub. Funcionan, pero son una contraseña de larga duración que hay que rotar.

## Consecuencias

- El entorno completo se recrea con dos comandos y se elimina con uno (`az group delete`).
- Cada despliegue crea una revisión nueva; si la sonda de readiness no pasa, el tráfico sigue en la anterior.
- Crear la asignación de rol AcrPull requiere permisos de propietario sobre el grupo de recursos.
- La misma imagen corre en local, en CI y en Azure; mover el servicio a otra nube solo requiere cambiar la infraestructura.
