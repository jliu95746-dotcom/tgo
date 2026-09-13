. (Join-Path $PSScriptRoot '../common.ps1')

# Capture settings without reading credentials or changing the real environment.
$script:crawlerEnvironment = @{}
function Import-DotEnv { param([string]$Path) }
function Get-EnvValue {
    param([string]$Name, [string]$DefaultValue)
    if ($script:crawlerEnvironment.ContainsKey($Name)) { return $script:crawlerEnvironment[$Name] }
    return $DefaultValue
}
function Set-ProcessEnv {
    param([string]$Name, [string]$Value)
    $script:crawlerEnvironment[$Name] = $Value
}

Set-NativeEnvironment -Service rag
$crawlerExpectedCache = Join-Path $script:RuntimeDir 'crawler-cache'
if ($script:crawlerEnvironment['CRAWL4_AI_BASE_DIRECTORY'] -ne $crawlerExpectedCache) {
    throw 'Native RAG must keep the crawler cache inside the project runtime directory'
}
$script:crawlerEnvironment['CRAWL4_AI_BASE_DIRECTORY'] = Join-Path $script:RuntimeDir 'custom-crawler-cache'
Set-NativeEnvironment -Service rag
if ($script:crawlerEnvironment['CRAWL4_AI_BASE_DIRECTORY'] -ne (Join-Path $script:RuntimeDir 'custom-crawler-cache')) {
    throw 'Explicit crawler cache directory must be preserved'
}
Write-Output 'Native crawler environment: 2 checks passed'

# The shell must hand PID 1 to Celery so Docker SIGTERM reaches the worker.
$crawlerComposeRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../..'))
foreach ($crawlerComposeFile in @('docker-compose.yml', 'docker-compose.dev.yml')) {
    $crawlerComposeText = Get-Content -Raw -LiteralPath (Join-Path $crawlerComposeRoot $crawlerComposeFile)
    $crawlerWorkerBlock = [regex]::Match($crawlerComposeText, '(?ms)^  tgo-rag-worker:\r?\n.*?(?=^  [a-zA-Z]|\z)').Value
    if ($crawlerWorkerBlock -notmatch '\bexec (?:/app/\.venv/bin/)?celery -A src\.rag_service\.tasks\.celery_app worker') {
        throw "RAG worker must forward shutdown signals: $crawlerComposeFile"
    }
}
Write-Output 'RAG worker shutdown configuration: 2 checks passed'
