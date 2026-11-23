# Despliegue en Azure Container Apps

Esta guía describe cómo se publica el chatbot en Azure: qué recursos se crean, cómo se hace el primer despliegue a mano desde Windows y cómo se configura el despliegue continuo desde GitHub Actions. La infraestructura está definida como código en `infra/main.bicep` y el despliegue continuo en `.github/workflows/cd.yml`.

Todos los comandos están escritos para PowerShell (7 o superior recomendado; Windows PowerShell 5.1 también sirve) y se ejecutan desde la raíz del repositorio.

## Qué se crea y por qué

La imagen Docker de la app (FastAPI servida por uvicorn en el puerto 8000) se guarda en un registro privado y se ejecuta en Azure Container Apps, que publica una URL HTTPS sin administrar servidores. El LLM y los embeddings son APIs externas, así que no se necesita GPU.

| Recurso | Nombre (con el prefijo por defecto `chatbotrag`) | Para qué |
| --- | --- | --- |
| Grupo de recursos | el que se elija, p. ej. `rg-chatbot-llm-rag` | Agrupa todo; al borrarlo se elimina todo lo demás. |
| Área de trabajo de Log Analytics | `chatbotrag-logs` | Recibe los logs de consola (JSON, con `LOG_FORMATO=json`) y los del sistema. Retención de 30 días y límite diario de ingesta de 1 GB para acotar el costo. |
| Azure Container Registry (Basic) | `chatbotrag` + sufijo único | Guarda las imágenes. El usuario administrador está deshabilitado: se accede con identidades de Entra ID, sin contraseñas. |
| Identidad administrada asignada por el usuario | `chatbotrag-id-acrpull` | Identidad con la que la app descarga su imagen del registro. |
| Asignación del rol AcrPull | GUID generado por la plantilla | Da a esa identidad permiso de solo lectura sobre el registro. |
| Entorno de Container Apps (perfil Consumption) | `chatbotrag-env` | Red, ingreso HTTPS y envío de logs a Log Analytics. Con el perfil Consumption se paga por uso. |
| Container App | `chatbotrag-app` | Ejecuta el contenedor con 0.5 vCPU y 1 GiB: ingreso externo HTTPS, secretos, variables de entorno, sondas de salud y una sola réplica. |

### Decisiones de diseño

- Una sola réplica (`maxReplicas = 1`). La app guarda pedidos, usuarios y conversaciones en SQLite dentro del disco del contenedor, que es efímero. Dos réplicas tendrían dos bases distintas. Cada revisión nueva (cada despliegue, cambio de escala o reinicio) empieza con una base vacía.
- Despliegue en dos fases con el parámetro `desplegarApp`. La Container App necesita que la imagen exista en el registro, y el registro no existe hasta que se despliega la plantilla. Por eso la fase 1 crea todo menos la app, luego se sube la imagen y la fase 2 crea la app.
- Secretos de Container Apps. Las claves de las APIs y el token de administración se guardan como secrets de la app y se inyectan como variables de entorno con `secretRef`; nunca quedan en el repositorio ni en el historial del despliegue (los parámetros son `@secure()`). Un secreto opcional vacío (`LLM_2_API_KEY`) no crea secret ni variable, porque Container Apps rechaza secrets vacíos.
- Sondas de salud ajustadas a un arranque de unos 60 s mientras se indexan los documentos:

| Sonda | Ruta | Comportamiento |
| --- | --- | --- |
| Startup | `/api/health/live` | Espera hasta unos 110 s a que el proceso responda; mientras tanto no corren las otras sondas. |
| Liveness | `/api/health/live` | Reinicia el contenedor solo si deja de responder unos 90 s seguidos. |
| Readiness | `/api/health/ready` | No envía tráfico hasta que el índice está listo (la ruta responde 503 mientras arranca). No reinicia el contenedor. |

## Prerrequisitos

- Una suscripción de Azure. La prueba gratuita suele durar 30 días e incluye un crédito inicial. Conviene revisar la fecha de vencimiento y el crédito restante en el portal (Suscripciones, o Administración de costos + facturación). Al terminar la prueba los recursos se deshabilitan salvo que la suscripción se convierta a pago por uso.
- Una alerta de presupuesto, creada antes de desplegar: en el portal, Administración de costos + facturación > Presupuestos > Agregar, con un monto mensual bajo y alertas por correo al 50 %, 80 % y 100 %. La alerta avisa, pero no detiene los recursos.
- Permisos de Propietario (Owner) sobre la suscripción o el grupo de recursos, porque la plantilla crea una asignación de rol. Con solo Colaborador (Contributor) la fase 1 falla en ese paso. Quien crea una prueba gratuita ya es Propietario.
- Azure CLI reciente con Bicep:

```powershell
winget install --exact --id Microsoft.AzureCLI
az bicep install
az version
```

- Docker Desktop en modo de contenedores Linux, en ejecución.
- Git y GitHub CLI (`gh`) para el despliegue continuo:

```powershell
winget install --exact --id GitHub.cli
gh auth login
```

## Despliegue manual paso a paso

### 1. Autenticarse en Azure

```powershell
az login
az account show --query "{suscripcion:name, id:id, tenant:tenantId}" -o table

# Solo si hay varias suscripciones
az account set --subscription "<nombre-o-id-de-la-suscripcion>"
```

### 2. Registrar los proveedores de recursos

En suscripciones nuevas puede que no estén registrados. Registrarlos de antemano evita el error `MissingSubscriptionRegistration`.

```powershell
'Microsoft.App', 'Microsoft.ContainerRegistry', 'Microsoft.OperationalInsights', 'Microsoft.ManagedIdentity' |
  ForEach-Object { az provider register --namespace $_ --wait }
```

### 3. Definir las variables de trabajo

Estas variables solo viven en la ventana de PowerShell actual; si se cierra, se vuelven a definir.

```powershell
$GRUPO     = 'rg-chatbot-llm-rag'
$UBICACION = 'eastus2'      # cualquier región con Container Apps; az account list-locations -o table
$PREFIJO   = 'chatbotrag'   # 3 a 20 caracteres: minúsculas, números y guiones; empieza con letra
```

### 4. Crear el grupo de recursos

```powershell
az group create --name $GRUPO --location $UBICACION
```

### 5. Fase 1: infraestructura base

Opcionalmente se revisa antes qué se va a crear con `what-if`:

```powershell
az deployment group what-if `
  --resource-group $GRUPO `
  --template-file infra/main.bicep `
  --parameters prefijo=$PREFIJO desplegarApp=false
```

Despliegue de la fase 1 (tarda unos minutos, sobre todo el entorno de Container Apps):

```powershell
az deployment group create `
  --resource-group $GRUPO `
  --name fase1 `
  --template-file infra/main.bicep `
  --parameters prefijo=$PREFIJO desplegarApp=false

$ACR      = az deployment group show --resource-group $GRUPO --name fase1 --query properties.outputs.acrNombre.value -o tsv
$REGISTRO = az deployment group show --resource-group $GRUPO --name fase1 --query properties.outputs.acrLoginServer.value -o tsv
$APP      = az deployment group show --resource-group $GRUPO --name fase1 --query properties.outputs.appNombre.value -o tsv
"$ACR | $REGISTRO | $APP"
```

### 6. Construir y subir la imagen

La etiqueta puede ser cualquiera; se usa el SHA del commit para que coincida con lo que hace el despliegue continuo. Si la carpeta aún no es un repositorio Git, sirve una etiqueta como `v1`.

```powershell
$TAG    = git rev-parse HEAD
$IMAGEN = "$REGISTRO/chatbot-llm-rag:$TAG"

docker build --platform linux/amd64 --tag $IMAGEN .
az acr login --name $ACR
docker push $IMAGEN

az acr repository show-tags --name $ACR --repository chatbot-llm-rag -o table
```

`az acr login` usa la identidad de Entra ID con la que se ejecutó `az login`; no hay usuario ni contraseña del registro.

### 7. Fase 2: crear la Container App

Los secretos se leen con `Read-Host` y se guardan solo en variables de entorno de la ventana actual. Así no quedan en el historial de PowerShell ni en ningún archivo.

```powershell
function Read-Secreto([string]$Nombre) {
    $valor = Read-Host -Prompt $Nombre -AsSecureString
    [System.Net.NetworkCredential]::new('', $valor).Password
}

$env:LLM_API_KEY        = Read-Secreto 'LLM_API_KEY'
$env:EMBEDDINGS_API_KEY = Read-Secreto 'EMBEDDINGS_API_KEY'
$env:LLM_2_API_KEY      = Read-Secreto 'LLM_2_API_KEY (Enter para omitir el respaldo)'
```

`ADMIN_TOKEN` protege `/api/logs`, `/api/metricas`, `/api/sesion` y `/api/ventas.csv`, y es obligatorio con `ENTORNO=produccion`. Se genera uno aleatorio y se copia al portapapeles para guardarlo en un gestor de contraseñas:

```powershell
$bytes = [byte[]]::new(32)
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:ADMIN_TOKEN = -join ($bytes | ForEach-Object { $_.ToString('x2') })
$env:ADMIN_TOKEN | Set-Clipboard
```

El acceso de la demo también es obligatorio en producción: un usuario (`ACCESO_USUARIO`) y el hash de su clave (`ACCESO_CLAVE_HASH`). La clave nunca va a Azure; se genera el hash desde la raíz del repositorio y se guarda solo la parte después del `=`:

```powershell
$ACCESO_USUARIO = Read-Host 'Usuario de acceso'
uv run python scripts/hash_clave.py   # pide la clave dos veces; copiar el valor entre comillas
$env:ACCESO_CLAVE_HASH = Read-Secreto 'ACCESO_CLAVE_HASH'
```

El script `infra/crear-app.ps1` hace todo el paso 7 de una vez: pide las claves sin mostrarlas, calcula el hash de la clave de acceso, genera el `ADMIN_TOKEN` y crea la app con la imagen más reciente del registro. Se ejecuta desde la raíz del repositorio con `powershell -ExecutionPolicy Bypass -File infra\crear-app.ps1`. Por defecto usa solo Qwen, con una clave de Model Studio para el chat (`qwen-plus`), el respaldo (`qwen-turbo`) y los embeddings; `-Proveedor deepseek` usa DeepSeek para el chat, y `-Region cn` apunta a la región de Pekín si la clave es de allí.

La configuración no secreta debe coincidir con la del `.env` local. Los valores siguientes son de ejemplo:

```powershell
$LLM_BASE_URL        = 'https://api.deepseek.com'
$LLM_MODEL           = 'deepseek-chat'
$LLM_2_BASE_URL      = 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
$LLM_2_MODEL         = 'qwen-plus'
$EMBEDDINGS_BASE_URL = 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
$EMBEDDINGS_MODEL    = 'text-embedding-v4'
```

Se arma la lista de parámetros y se despliega. Si `LLM_2_API_KEY` quedó vacía, no se envía y la app no recibe ni el secret ni la variable.

```powershell
$parametros = @(
    "prefijo=$PREFIJO"
    'desplegarApp=true'
    "imagen=$IMAGEN"
    'minReplicas=1'
    "llmApiKey=$env:LLM_API_KEY"
    "embeddingsApiKey=$env:EMBEDDINGS_API_KEY"
    "adminToken=$env:ADMIN_TOKEN"
    "accesoUsuario=$ACCESO_USUARIO"
    "accesoClaveHash=$env:ACCESO_CLAVE_HASH"
    "llmBaseUrl=$LLM_BASE_URL"
    "llmModel=$LLM_MODEL"
    "llm2BaseUrl=$LLM_2_BASE_URL"
    "llm2Model=$LLM_2_MODEL"
    "embeddingsBaseUrl=$EMBEDDINGS_BASE_URL"
    "embeddingsModel=$EMBEDDINGS_MODEL"
)
if ($env:LLM_2_API_KEY) { $parametros += "llm2ApiKey=$env:LLM_2_API_KEY" }

az deployment group create `
  --resource-group $GRUPO `
  --name fase2 `
  --template-file infra/main.bicep `
  --parameters $parametros
```

La plantilla también acepta `variablesAdicionales`, un objeto con otras variables no secretas (por ejemplo `LOG_NIVEL` o `LIMITE_CHAT_POR_MINUTO`). Desde PowerShell se pasa con las comillas escapadas: `$parametros += 'variablesAdicionales={\"LOG_NIVEL\":\"DEBUG\"}'`.

Notas sobre la fase 2:

- Cada vez que se vuelve a ejecutar, la plantilla define la lista completa de secretos, así que hay que volver a pasar todos.
- Si el despliegue continuo ya actualizó la imagen, se debe pasar la imagen actual para no volver a una versión anterior:

```powershell
$IMAGEN = az containerapp show --name $APP --resource-group $GRUPO --query "properties.template.containers[0].image" -o tsv
```

- Volver a ejecutar la fase 1 no toca la Container App: los despliegues son incrementales y la fase 1 no la incluye.

### 8. Obtener la URL y verificar la salud

```powershell
$URL = az deployment group show --resource-group $GRUPO --name fase2 --query properties.outputs.appUrl.value -o tsv
$URL

# Proceso vivo: responde 200 en cuanto arranca uvicorn
curl.exe -s "$URL/api/health/live"

# Listo para atender: 503 mientras se indexan los documentos (hasta ~60 s), luego 200
for ($i = 1; $i -le 18; $i++) {
    $codigo = curl.exe -s -o NUL -w '%{http_code}' "$URL/api/health/ready"
    "Intento ${i}: HTTP $codigo"
    if ($codigo -eq '200') { break }
    Start-Sleep -Seconds 10
}

# Estado general: modo (real o demo), documentos y catálogo indexados y parámetros, sin claves
curl.exe -s "$URL/api/health"

# Endpoint protegido: requiere el encabezado X-Admin-Token
curl.exe -s -H "X-Admin-Token: $env:ADMIN_TOKEN" "$URL/api/metricas"
```

Si `/api/health` informa `"modo": "demo"`, la app arrancó sin un LLM configurado: falta la URL, el modelo o la clave del LLM principal.

## Despliegue continuo con GitHub Actions (OIDC)

El workflow `CD` se ejecuta cuando el workflow `CI` termina con éxito en un push a `main`, o a mano desde la pestaña Actions. Construye la imagen en el runner de GitHub, la sube al registro con `docker push` (no usa `az acr build`, porque ACR Tasks puede no estar disponible en suscripciones de prueba), actualiza la Container App con la imagen etiquetada con el SHA del commit y espera hasta unos 3 minutos a que `/api/health/ready` responda 200 en la revisión nueva; si no, el job falla.

La autenticación usa OpenID Connect: GitHub emite un token de corta duración que Entra ID acepta gracias a una credencial federada. No se guarda ninguna contraseña ni clave de Azure en GitHub.

Requisitos previos: la Container App ya existe (fase 2 completada) y el archivo `.github/workflows/cd.yml` está en la rama por defecto del repositorio (`dev`), porque GitHub lee los disparadores `workflow_run` solo desde esa rama. Por la misma razón, la ejecución del CD figura sobre la rama por defecto aunque despliega el commit de `main` que pasó el CI.

### 1. Variables de trabajo

Se reutilizan `$GRUPO`, `$ACR` y `$APP` de los pasos anteriores.

```powershell
$REPO        = 'MechatronicCoder/chatbot-llm-rag'
$SUSCRIPCION = az account show --query id -o tsv
$TENANT      = az account show --query tenantId -o tsv
```

### 2. Registro de aplicación y entidad de servicio

```powershell
$APP_ID = az ad app create --display-name 'github-chatbot-llm-rag-cd' --query appId -o tsv
$SP_ID  = az ad sp create --id $APP_ID --query id -o tsv
"$APP_ID | $SP_ID"
```

Si el inquilino de Entra ID no permite que los usuarios registren aplicaciones, este paso lo debe hacer un administrador.

### 3. Credencial federada

El `subject` debe coincidir exactamente con el repositorio y con el environment `produccion` que usa el job. El archivo temporal no contiene secretos y se borra al terminar.

```powershell
$archivo = Join-Path $env:TEMP 'credencial-oidc.json'
@"
{
  "name": "github-produccion",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:${REPO}:environment:produccion",
  "description": "Despliegue continuo desde el environment produccion",
  "audiences": ["api://AzureADTokenExchange"]
}
"@ | Set-Content -Path $archivo -Encoding ascii

az ad app federated-credential create --id $APP_ID --parameters "@$archivo"
Remove-Item $archivo
```

### 4. Roles de la entidad de servicio

- Contributor sobre el grupo de recursos, para actualizar la Container App.
- AcrPush sobre el registro, para subir imágenes.

```powershell
$GRUPO_ID = az group show --name $GRUPO --query id -o tsv
$ACR_ID   = az acr show --name $ACR --query id -o tsv

az role assignment create --assignee-object-id $SP_ID --assignee-principal-type ServicePrincipal `
  --role Contributor --scope $GRUPO_ID
az role assignment create --assignee-object-id $SP_ID --assignee-principal-type ServicePrincipal `
  --role AcrPush --scope $ACR_ID
```

### 5. Environment y variables en GitHub

```powershell
gh api --method PUT "repos/$REPO/environments/produccion"

gh variable set AZURE_CLIENT_ID       --repo $REPO --body $APP_ID
gh variable set AZURE_TENANT_ID       --repo $REPO --body $TENANT
gh variable set AZURE_SUBSCRIPTION_ID --repo $REPO --body $SUSCRIPCION
gh variable set AZURE_RESOURCE_GROUP  --repo $REPO --body $GRUPO
gh variable set ACR_NOMBRE            --repo $REPO --body $ACR
gh variable set CONTAINERAPP_NOMBRE   --repo $REPO --body $APP

gh variable list --repo $REPO
```

- Son variables de repositorio, no del environment: la condición del job se evalúa antes de entrar al environment y necesita leer `AZURE_CLIENT_ID`. Mientras esa variable esté vacía, el job se omite en lugar de fallar.
- No son secretos: son identificadores. Lo que protege el acceso es la credencial federada, que solo acepta tokens de este repositorio y este environment.
- Para exigir una aprobación manual antes de cada despliegue se agrega un revisor en Settings > Environments > produccion > Required reviewers. No se limita el environment a la rama `main`: el CD corre sobre la rama por defecto y esa regla lo bloquearía.

### 6. Probar el workflow

```powershell
gh workflow run cd.yml --repo $REPO --ref main
gh run list --workflow cd.yml --repo $REPO --limit 3
```

Después, cada push a `main` que pase CI despliega solo. Los pull requests no despliegan, aunque CI pase en ellos.

## Logs

### Desde la terminal

```powershell
# Logs de la app (stdout), en vivo
az containerapp logs show --name $APP --resource-group $GRUPO --tail 50 --follow

# Logs del sistema: descarga de la imagen, sondas, reinicios
az containerapp logs show --name $APP --resource-group $GRUPO --type system --tail 50 --format text
```

### En Log Analytics

En el portal: área de trabajo `chatbotrag-logs` > Registros. Los logs tardan unos minutos en aparecer. Cada línea de la app es un JSON con los campos `ts`, `nivel`, `evento` y `turno`, que se extraen con `parse_json`.

Últimos eventos de la app:

```kusto
ContainerAppConsoleLogs_CL
| where ContainerAppName_s == 'chatbotrag-app'
| where TimeGenerated > ago(1h)
| extend e = parse_json(Log_s)
| project TimeGenerated, RevisionName_s, nivel = tostring(e.nivel), evento = tostring(e.evento), turno = tostring(e.turno), Log_s
| order by TimeGenerated desc
| take 100
```

Errores por evento y por hora:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(24h)
| extend e = parse_json(Log_s)
| where tostring(e.nivel) == 'ERROR'
| summarize errores = count() by evento = tostring(e.evento), bin(TimeGenerated, 1h)
| order by TimeGenerated desc
```

Reconstrucción de un turno de conversación: se añade `| where tostring(e.turno) == 't-1a2b3c4d'` a la primera consulta.

Eventos del sistema (útil cuando la revisión no arranca):

```kusto
ContainerAppSystemLogs_CL
| where ContainerAppName_s == 'chatbotrag-app'
| project TimeGenerated, RevisionName_s, Reason_s, Log_s
| order by TimeGenerated desc
| take 50
```

Las mismas consultas se pueden ejecutar desde la terminal; el comando instala la extensión `log-analytics` la primera vez:

```powershell
az extension add --name log-analytics
$WORKSPACE = az monitor log-analytics workspace show --resource-group $GRUPO --workspace-name "$PREFIJO-logs" --query customerId -o tsv
az monitor log-analytics query --workspace $WORKSPACE --analytics-query "ContainerAppConsoleLogs_CL | take 20" -o table
```

## Costos y cómo bajarlos

Los precios cambian por región y con el tiempo, así que se consultan en la [calculadora de precios de Azure](https://azure.microsoft.com/es-es/pricing/calculator/) y en la [página de precios de Container Apps](https://azure.microsoft.com/es-es/pricing/details/container-apps/). Lo que genera costo:

| Recurso | Cómo se cobra |
| --- | --- |
| Container App (Consumption) | Por vCPU-segundo y GiB-segundo mientras hay una réplica encendida, más las solicitudes. Hay una cuota gratuita mensual por suscripción. Con `minReplicas = 1` la réplica nunca se apaga; sin tráfico se cobra a la tarifa reducida de inactividad. |
| Container Registry Basic | Monto fijo diario, con algo de almacenamiento incluido. |
| Log Analytics | Por GB ingerido, con una cuota gratuita mensual. El límite diario de 1 GB (`limiteDiarioLogsGb`) acota el gasto; al alcanzarlo se dejan de guardar logs hasta el día siguiente. |
| APIs externas (LLM y embeddings) | Las factura cada proveedor, fuera de Azure. |

Formas de bajar el costo:

- `minReplicas = 0`. La app se apaga tras unos minutos sin solicitudes y no consume cómputo mientras está apagada. La primera solicitud después de apagarse espera el arranque completo (descarga de la imagen e indexado, más de un minuto). Además, cada apagado borra la base SQLite (pedidos, usuarios y conversaciones) y obliga a recalcular los embeddings de los documentos y del catálogo, lo que genera llamadas extra a la API de embeddings. Sirve para una demo; no para guardar datos.

  Para que el cambio sea permanente se vuelve a ejecutar la fase 2 con `minReplicas=0` en `$parametros`. El atajo siguiente también funciona, pero la próxima ejecución de la fase 2 vuelve a aplicar el valor del parámetro:

```powershell
az containerapp update --name $APP --resource-group $GRUPO --min-replicas 0
```

- Borrar imágenes viejas del registro: cada despliegue continuo sube una etiqueta nueva.

```powershell
az acr repository show-tags --name $ACR --repository chatbot-llm-rag --orderby time_asc -o table
az acr repository delete --name $ACR --image chatbot-llm-rag:<etiqueta-vieja> --yes
```

- Borrar todo al terminar de usarlo (sección siguiente). Es la única forma de que el costo baje a cero.

## Cómo borrar todo

Borrar el grupo de recursos elimina la app, el entorno, el registro con sus imágenes, la identidad, los logs y las asignaciones de rol dentro del grupo. No se puede deshacer.

```powershell
az group delete --name $GRUPO --yes --no-wait
az group exists --name $GRUPO   # false cuando terminó
```

El registro de aplicación de Entra ID vive fuera del grupo y se borra aparte. Al borrar `AZURE_CLIENT_ID` en GitHub, el workflow de CD vuelve a omitirse sin fallar:

```powershell
az ad app delete --id $APP_ID
gh variable delete AZURE_CLIENT_ID --repo $REPO
```

## Solución de problemas

### La revisión no arranca o se reinicia en bucle

```powershell
az containerapp revision list --name $APP --resource-group $GRUPO `
  --query "[].{revision:name, activa:properties.active, salud:properties.healthState, estado:properties.runningState}" -o table
az containerapp logs show --name $APP --resource-group $GRUPO --type system --tail 50 --format text
az containerapp logs show --name $APP --resource-group $GRUPO --tail 50
```

Causas habituales:

- La app falla al validar la configuración y termina. Por ejemplo, con `ENTORNO=produccion` exige `ADMIN_TOKEN`, y rechaza valores fuera de rango. El mensaje aparece en los logs de consola.
- uvicorn no escucha en `0.0.0.0:8000`. Si escucha en `127.0.0.1` o en otro puerto, las sondas fallan.
- La imagen se construyó para otra arquitectura (`exec format error`): se reconstruye con `--platform linux/amd64`.
- Falta de memoria (`OOMKilled` en los logs del sistema): la app usa más de 1 GiB.

### 401 o unauthorized al descargar la imagen

- La asignación del rol AcrPull puede tardar unos minutos en propagarse después de la fase 1. Se espera y se repite la fase 2.
- Se comprueba que la identidad tiene el rol y que la imagen existe con esa etiqueta (`MANIFEST_UNKNOWN` indica una etiqueta inexistente):

```powershell
$ACR_ID = az acr show --name $ACR --query id -o tsv
az role assignment list --scope $ACR_ID --query "[].{rol:roleDefinitionName, principal:principalName}" -o table
az acr repository show-tags --name $ACR --repository chatbot-llm-rag -o table
az containerapp show --name $APP --resource-group $GRUPO --query properties.configuration.registries
```

- Si el error aparece en el workflow al hacer `docker push`, a la entidad de servicio le falta AcrPush sobre el registro (o el rol aún no se propaga).

### La sonda de readiness no pasa

- Durante el primer minuto es normal: `/api/health/ready` responde 503 mientras se indexan los documentos y el catálogo.
- Si sigue en 503, `/api/health` muestra el estado de los documentos (`corpus`) y del catálogo (`catalogo`), cada uno con `indexado` y `aviso`, y si la app está en modo real o demo. La causa más común es una clave, URL o modelo de embeddings incorrectos, o que el proveedor no responda. Los logs de consola muestran el error del indexado.
- Mientras la revisión nueva no esté lista, el tráfico sigue en la revisión anterior (si existe). En el primer despliegue la URL responde con error hasta que la revisión esté lista.

### Error por un secret vacío

Container Apps rechaza un secret sin valor, así que la fase 2 falla si `llmApiKey`, `embeddingsApiKey` o `adminToken` llegan vacíos (secrets `llm-api-key`, `embeddings-api-key` y `admin-token`). Casi siempre es porque se abrió una ventana nueva de PowerShell y las variables de entorno ya no existen. Se comprueba así, sin mostrar los valores:

```powershell
'LLM_API_KEY', 'EMBEDDINGS_API_KEY', 'ADMIN_TOKEN', 'LLM_2_API_KEY' |
  ForEach-Object { '{0}: {1}' -f $_, [bool][Environment]::GetEnvironmentVariable($_) }
```

`LLM_2_API_KEY` es opcional: si está vacía, la plantilla no crea ni el secret ni la variable.

En Windows, `az` se ejecuta a través de un archivo `.cmd`, así que un valor con `&`, `|`, `<`, `>`, `^` o `%` puede cortarse. Las claves habituales de los proveedores y el token hexadecimal generado arriba no tienen esos caracteres.

### Cambiar una clave sin volver a desplegar

```powershell
$env:LLM_API_KEY = Read-Secreto 'LLM_API_KEY'
az containerapp secret set --name $APP --resource-group $GRUPO --secrets "llm-api-key=$env:LLM_API_KEY"
$REVISION = az containerapp show --name $APP --resource-group $GRUPO --query properties.latestRevisionName -o tsv
az containerapp revision restart --name $APP --resource-group $GRUPO --revision $REVISION
```

El reinicio es necesario para que el contenedor lea el valor nuevo, y también borra la base SQLite. Si después se ejecuta la fase 2, hay que pasar la clave nueva.

La clave del acceso de la demo se cambia igual, con el secreto `acceso-clave-hash` y el hash nuevo que genera `scripts/hash_clave.py`. El reinicio cierra todas las sesiones abiertas.

### El workflow de CD no corre o falla

- Job omitido (skipped): `AZURE_CLIENT_ID` está vacía, CI no terminó en éxito, CI se ejecutó por un pull request y no por un push a `main`, o el workflow de integración no se llama exactamente `CI`.
- `AADSTS70021: No matching federated identity record found`: el `subject` de la credencial federada no coincide con `repo:MechatronicCoder/chatbot-llm-rag:environment:produccion` (revisar dueño, nombre del repositorio y nombre del environment).
- `AuthorizationFailed` al actualizar la app: falta el rol Contributor sobre el grupo de recursos o aún no se propagó.
- La prueba de humo agota los intentos: el job imprime el estado de las revisiones y los últimos logs del sistema. Se revisa con las secciones anteriores.

### Errores de región o de cuota

- `MissingSubscriptionRegistration`: falta registrar un proveedor (paso 2).
- Errores de cuota o de disponibilidad en la región: las suscripciones de prueba tienen cuotas bajas. Se borra el grupo de recursos y se repite el proceso en otra región.
