# Crea la Container App del chatbot en Azure (fase 2 de docs/despliegue-azure.md).
# Pide las claves sin mostrarlas y no las guarda en ningún archivo. Requiere la fase 1
# hecha y una imagen en el registro. Desde la raíz del repositorio:
#   powershell -ExecutionPolicy Bypass -File infra\crear-app.ps1
# Proveedor 'qwen' (por defecto): una sola clave de Model Studio para el chat (qwen-plus),
# el respaldo (qwen-turbo) y los embeddings. Proveedor 'deepseek': DeepSeek para el chat
# y Qwen para el respaldo y los embeddings.
# Region: 'intl' (Singapur, dashscope-intl) o 'cn' (Pekín); debe coincidir con la de la clave.
# MinReplicas 0 = la app se apaga sin tráfico: ahorra crédito, pero tarda en despertar
# y borra los pedidos.
param(
    [string]$Grupo = 'rg-chatbot-llm-rag',
    [string]$Prefijo = 'chatbotrag',
    [ValidateSet('qwen', 'deepseek')][string]$Proveedor = 'qwen',
    [ValidateSet('intl', 'cn')][string]$Region = 'intl',
    [ValidateRange(0, 1)][int]$MinReplicas = 1
)

$ErrorActionPreference = 'Stop'
$REPO = Split-Path -Parent $PSScriptRoot
$URL_QWEN = if ($Region -eq 'cn') {
    'https://dashscope.aliyuncs.com/compatible-mode/v1'
} else {
    'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
}

function Read-Secreto([string]$Nombre) {
    $valor = Read-Host -Prompt $Nombre -AsSecureString
    [System.Net.NetworkCredential]::new('', $valor).Password
}

# Mismo formato que app/core/claves.py: pbkdf2_sha256$<iteraciones>$<sal>$<derivada>
function New-HashClave([string]$Clave) {
    $sal = [byte[]]::new(16)
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($sal)
    $kdf = [System.Security.Cryptography.Rfc2898DeriveBytes]::new(
        $Clave, $sal, 600000, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
    $derivada = $kdf.GetBytes(32)
    $kdf.Dispose()
    'pbkdf2_sha256${0}${1}${2}' -f 600000, [Convert]::ToBase64String($sal), [Convert]::ToBase64String($derivada)
}

# La plantilla debe conocer el acceso de la demo (rama dev o main actualizada).
if (-not (Select-String -Path (Join-Path $REPO 'infra\main.bicep') -Pattern 'accesoClaveHash' -Quiet)) {
    throw 'infra/main.bicep no tiene el acceso de la demo: en el repositorio ejecuta "git switch dev" y "git pull".'
}

Write-Host "Suscripción activa: $(az account show --query name -o tsv)"
$ACR = az acr list --resource-group $Grupo --query '[0].name' -o tsv
if (-not $ACR) { throw "No hay un registro de imágenes en el grupo ${Grupo}: falta la fase 1." }
$tag = az acr repository show-tags --name $ACR --repository chatbot-llm-rag --orderby time_desc --top 1 -o tsv
if (-not $tag) { throw "No hay imágenes en el registro $ACR." }
$IMAGEN = "$ACR.azurecr.io/chatbot-llm-rag:$tag"
Write-Host "Imagen a desplegar (la más reciente del registro): $IMAGEN"

$qwenKey = Read-Secreto "Clave de Qwen en Model Studio (región $Region)"
if (-not $qwenKey) { throw 'Falta la clave de Qwen.' }
if ($Proveedor -eq 'deepseek') {
    $llmKey = Read-Secreto 'Clave de DeepSeek (LLM_API_KEY)'
    if (-not $llmKey) { throw 'Falta la clave de DeepSeek.' }
    $llmUrl, $llmModelo = 'https://api.deepseek.com', 'deepseek-chat'
    $respaldoModelo = 'qwen-plus'
} else {
    $llmKey = $qwenKey
    $llmUrl, $llmModelo = $URL_QWEN, 'qwen-plus'
    $respaldoModelo = 'qwen-turbo'   # otro modelo, misma clave: cubre fallas de un modelo
}
Write-Host "Chat: $llmModelo · respaldo: $respaldoModelo · embeddings: text-embedding-v4"

# Acceso de la demo: en Azure solo se guarda el hash de la clave, como secreto.
# El usuario tampoco va en el repositorio público: se escribe al ejecutar el script.
$usuarioAcceso = (Read-Host 'Usuario de acceso a la demo').Trim()
if ($usuarioAcceso -notmatch '^[A-Za-z0-9._@-]{3,60}$') {
    throw 'El usuario debe tener de 3 a 60 caracteres: letras, números, punto, guion, guion bajo o @.'
}
$claveAcceso = Read-Secreto 'Clave de acceso a la demo (mínimo 10 caracteres)'
if ($claveAcceso.Length -lt 10) { throw 'La clave de acceso debe tener al menos 10 caracteres.' }
if ((Read-Secreto 'Repite la clave de acceso') -ne $claveAcceso) { throw 'Las claves de acceso no coinciden.' }
$hashAcceso = New-HashClave $claveAcceso

# ADMIN_TOKEN protege logs, métricas, historial y ventas; en producción es obligatorio.
$bytes = [byte[]]::new(32)
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$adminToken = -join ($bytes | ForEach-Object { $_.ToString('x2') })
$adminToken | Set-Clipboard
Write-Host 'Se generó el ADMIN_TOKEN y se copió al portapapeles. Guárdalo en tu gestor de contraseñas.'
Read-Host 'Pulsa Enter cuando lo hayas guardado' | Out-Null

$parametros = @(
    "prefijo=$Prefijo"
    'desplegarApp=true'
    "imagen=$IMAGEN"
    "minReplicas=$MinReplicas"
    "llmApiKey=$llmKey"
    "llm2ApiKey=$qwenKey"
    "embeddingsApiKey=$qwenKey"
    "adminToken=$adminToken"
    "accesoUsuario=$usuarioAcceso"
    "accesoClaveHash=$hashAcceso"
    "llmBaseUrl=$llmUrl"
    "llmModel=$llmModelo"
    "llm2BaseUrl=$URL_QWEN"
    "llm2Model=$respaldoModelo"
    "embeddingsBaseUrl=$URL_QWEN"
    'embeddingsModel=text-embedding-v4'
)

Push-Location $REPO
try {
    Write-Host 'Creando la app (tarda unos minutos)...'
    az deployment group create --resource-group $Grupo --name fase2 --template-file infra/main.bicep `
        --parameters $parametros --query properties.provisioningState -o tsv
    if ($LASTEXITCODE -ne 0) { throw 'El despliegue falló; revisa el mensaje de az arriba.' }
} finally {
    Pop-Location
    Remove-Variable llmKey, qwenKey, adminToken, claveAcceso, hashAcceso, parametros -ErrorAction SilentlyContinue
}

$URL = az deployment group show --resource-group $Grupo --name fase2 --query properties.outputs.appUrl.value -o tsv
Write-Host "URL de la app: $URL"
Write-Host 'Esperando a que indexe documentos y catálogo...'
for ($i = 1; $i -le 30; $i++) {
    $codigo = curl.exe -s -o NUL -w '%{http_code}' "$URL/api/health/ready"
    Write-Host "Intento ${i}: HTTP $codigo"
    if ($codigo -eq '200') { break }
    Start-Sleep -Seconds 10
}
Write-Host "Acceso: $(curl.exe -s "$URL/api/acceso/estado")   (debe decir requerido=true)"
curl.exe -s "$URL/api/health"
