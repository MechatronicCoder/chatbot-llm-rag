// Infraestructura del chatbot en Azure Container Apps.
//
// Despliegue en dos fases (ver docs/despliegue-azure.md):
//   Fase 1 (desplegarApp=false): Log Analytics, registro de contenedores, identidad
//          con AcrPull y entorno de Container Apps. Permite subir la imagen al registro.
//   Fase 2 (desplegarApp=true): además crea o actualiza la Container App con la imagen
//          ya subida, sus secretos, variables de entorno, sondas y escala.

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// Parámetros generales
// ---------------------------------------------------------------------------

@description('Región de los recursos. Por defecto, la del grupo de recursos.')
param location string = resourceGroup().location

@description('Prefijo de los nombres: minúsculas, números y guiones; debe empezar con letra y terminar en letra o número.')
@minLength(3)
@maxLength(20)
param prefijo string = 'chatbotrag'

@description('false: fase 1, crea todo menos la Container App. true: fase 2, crea o actualiza la Container App.')
param desplegarApp bool = false

@description('Imagen completa de la app: <acr>.azurecr.io/chatbot-llm-rag:<tag>. Obligatoria cuando desplegarApp=true.')
param imagen string = ''

@description('Réplicas mínimas. 1: siempre encendida. 0: se apaga por inactividad (más barato), pero se pierde la base SQLite al apagarse.')
@minValue(0)
@maxValue(1)
param minReplicas int = 1

@description('Días de retención de los logs en Log Analytics.')
@minValue(30)
@maxValue(730)
param diasRetencionLogs int = 30

@description('Límite diario de ingesta de logs en GB para acotar el costo (-1 = sin límite).')
param limiteDiarioLogsGb int = 1

// ---------------------------------------------------------------------------
// Secretos (solo se usan en la fase 2; nunca se escriben en archivos)
// ---------------------------------------------------------------------------

@secure()
@description('LLM_API_KEY: clave del LLM principal. Obligatoria en la fase 2.')
param llmApiKey string = ''

@secure()
@description('LLM_2_API_KEY: clave del LLM de respaldo. Opcional: si está vacía no se crea el secret ni la variable.')
param llm2ApiKey string = ''

@secure()
@description('EMBEDDINGS_API_KEY: clave de la API de embeddings. Obligatoria en la fase 2.')
param embeddingsApiKey string = ''

@secure()
@description('ADMIN_TOKEN: protege /api/logs, /api/metricas, /api/sesion y /api/ventas.csv. Obligatorio en producción.')
param adminToken string = ''

// ---------------------------------------------------------------------------
// Configuración no secreta (vacío = no se define la variable de entorno)
// ---------------------------------------------------------------------------

@description('LLM_BASE_URL: URL base del LLM principal (API compatible con OpenAI).')
param llmBaseUrl string = ''

@description('LLM_MODEL: modelo del LLM principal.')
param llmModel string = ''

@description('LLM_2_BASE_URL: URL base del LLM de respaldo.')
param llm2BaseUrl string = ''

@description('LLM_2_MODEL: modelo del LLM de respaldo.')
param llm2Model string = ''

@description('EMBEDDINGS_BASE_URL: URL base de la API de embeddings.')
param embeddingsBaseUrl string = ''

@description('EMBEDDINGS_MODEL: modelo de embeddings.')
param embeddingsModel string = ''

@description('Otras variables no secretas, como objeto nombre-valor (p. ej. {"LOG_NIVEL": "INFO"}). No repetir las que ya tienen parámetro propio.')
param variablesAdicionales object = {}

// ---------------------------------------------------------------------------
// Nombres y constantes
// ---------------------------------------------------------------------------

var prefijoNormalizado = toLower(prefijo)
var nombreLogs = '${prefijoNormalizado}-logs'
// El nombre del registro es global en Azure: solo letras y números, de 5 a 50 caracteres.
var nombreAcr = take('${replace(prefijoNormalizado, '-', '')}${uniqueString(resourceGroup().id)}', 50)
var nombreIdentidad = '${prefijoNormalizado}-id-acrpull'
var nombreEntorno = '${prefijoNormalizado}-env'
var nombreApp = '${prefijoNormalizado}-app'
var nombreContenedor = 'chatbot-llm-rag'
var puerto = 8000

// Rol integrado AcrPull: permite descargar imágenes del registro.
var rolAcrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

// ---------------------------------------------------------------------------
// Secretos y variables de entorno de la app
// ---------------------------------------------------------------------------

// Los obligatorios se envían siempre: si llegan vacíos, Container Apps rechaza el
// despliegue en lugar de arrancar una app sin credenciales.
var secretosObligatorios = [
  { name: 'llm-api-key', value: llmApiKey }
  { name: 'embeddings-api-key', value: embeddingsApiKey }
  { name: 'admin-token', value: adminToken }
]

// Los opcionales vacíos no crean secret (Container Apps rechaza secrets vacíos).
var secretosOpcionales = empty(llm2ApiKey) ? [] : [{ name: 'llm-2-api-key', value: llm2ApiKey }]

var variablesFijas = [
  { name: 'ENTORNO', value: 'produccion' }
  { name: 'LOG_FORMATO', value: 'json' }
  // El ingress de Container Apps agrega la IP real del cliente al final de X-Forwarded-For.
  { name: 'PROXIES_CONFIABLES', value: '1' }
  { name: 'LLM_API_KEY', secretRef: 'llm-api-key' }
  { name: 'EMBEDDINGS_API_KEY', secretRef: 'embeddings-api-key' }
  { name: 'ADMIN_TOKEN', secretRef: 'admin-token' }
]

var variablesSecretasOpcionales = empty(llm2ApiKey) ? [] : [{ name: 'LLM_2_API_KEY', secretRef: 'llm-2-api-key' }]

var variablesConfiguracion = union(
  {
    LLM_BASE_URL: llmBaseUrl
    LLM_MODEL: llmModel
    LLM_2_BASE_URL: llm2BaseUrl
    LLM_2_MODEL: llm2Model
    EMBEDDINGS_BASE_URL: embeddingsBaseUrl
    EMBEDDINGS_MODEL: embeddingsModel
  },
  variablesAdicionales
)

// Solo se definen las variables con valor.
var variablesTexto = map(
  filter(items(variablesConfiguracion), v => !empty(string(v.value))),
  v => { name: v.key, value: string(v.value) }
)

// ---------------------------------------------------------------------------
// Recursos de la fase 1
// ---------------------------------------------------------------------------

// Destino de los logs de consola y del sistema de Container Apps.
resource logs 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: nombreLogs
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: diasRetencionLogs
    workspaceCapping: {
      dailyQuotaGb: limiteDiarioLogsGb
    }
  }
}

// Registro privado de imágenes; sin usuario administrador (se accede con Entra ID).
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: nombreAcr
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

// Identidad con la que la Container App descarga la imagen del registro.
resource identidad 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: nombreIdentidad
  location: location
}

resource asignacionAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identidad.id, rolAcrPull)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', rolAcrPull)
    principalId: identidad.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Entorno de Container Apps con perfil Consumption (se paga por uso).
resource entorno 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: nombreEntorno
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

// ---------------------------------------------------------------------------
// Recurso de la fase 2
// ---------------------------------------------------------------------------

resource app 'Microsoft.App/containerApps@2024-03-01' = if (desplegarApp) {
  name: nombreApp
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identidad.id}': {}
    }
  }
  properties: {
    environmentId: entorno.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: puerto
        transport: 'auto'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: identidad.id
        }
      ]
      secrets: concat(secretosObligatorios, secretosOpcionales)
    }
    template: {
      containers: [
        {
          name: nombreContenedor
          image: imagen
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(variablesFijas, variablesSecretasOpcionales, variablesTexto)
          probes: [
            // Arranque: da hasta ~110 s para que el proceso responda antes de vigilarlo.
            {
              type: 'Startup'
              httpGet: {
                path: '/api/health/live'
                port: puerto
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 10
            }
            // Vida: reinicia el contenedor solo si el proceso deja de responder (~90 s).
            {
              type: 'Liveness'
              httpGet: {
                path: '/api/health/live'
                port: puerto
              }
              periodSeconds: 30
              timeoutSeconds: 5
              failureThreshold: 3
            }
            // Preparación: no recibe tráfico hasta que el índice de documentos esté listo
            // (503 mientras arranca, ~60 s); el margen cubre de sobra ese tiempo.
            {
              type: 'Readiness'
              httpGet: {
                path: '/api/health/ready'
                port: puerto
              }
              initialDelaySeconds: 10
              periodSeconds: 10
              timeoutSeconds: 5
              failureThreshold: 10
              successThreshold: 1
            }
          ]
        }
      ]
      // SQLite vive en el disco del contenedor: nunca más de una réplica.
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1
      }
    }
  }
  // La identidad debe tener AcrPull antes de que la app intente descargar la imagen.
  dependsOn: [
    asignacionAcrPull
  ]
}

// ---------------------------------------------------------------------------
// Salidas
// ---------------------------------------------------------------------------

@description('Nombre del registro de contenedores.')
output acrNombre string = acr.name

@description('Servidor de inicio de sesión del registro (<acr>.azurecr.io).')
output acrLoginServer string = acr.properties.loginServer

@description('Nombre de la Container App (existe solo tras la fase 2).')
output appNombre string = nombreApp

@description('Nombre del área de trabajo de Log Analytics.')
output logAnalyticsNombre string = logs.name

@description('FQDN público de la app; vacío si no se desplegó.')
output appFqdn string = desplegarApp ? app!.properties.configuration.ingress.fqdn : ''

@description('URL pública de la app; vacía si no se desplegó.')
output appUrl string = desplegarApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
