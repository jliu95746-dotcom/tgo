param(
    [switch]$SkipMigrations,
    [switch]$SkipRagWorker
)

. (Join-Path $PSScriptRoot 'common.ps1')

$apiDirectory = Join-Path $script:RepoRoot 'repos\tgo-api'
$aiDirectory = Join-Path $script:RepoRoot 'repos\tgo-ai'
$ragDirectory = Join-Path $script:RepoRoot 'repos\tgo-rag'
$workflowDirectory = Join-Path $script:RepoRoot 'repos\tgo-workflow'
$webDirectory = Join-Path $script:RepoRoot 'repos\tgo-web'
$widgetDirectory = Join-Path $script:RepoRoot 'repos\tgo-widget-js'
$apiPython = Join-Path $apiDirectory '.venv\Scripts\python.exe'
$aiPython = Join-Path $aiDirectory '.venv\Scripts\python.exe'
$ragPython = Join-Path $ragDirectory '.venv\Scripts\python.exe'
$workflowPython = Join-Path $workflowDirectory '.venv\Scripts\python.exe'
$viteScript = Join-Path $webDirectory 'node_modules\vite\bin\vite.js'
$widgetViteScript = Join-Path $widgetDirectory 'node_modules\vite\bin\vite.js'
$node = (Get-Command node -ErrorAction Stop).Source

foreach ($requiredPath in @(
    $apiPython,
    $aiPython,
    $ragPython,
    $workflowPython,
    $viteScript,
    $widgetViteScript
)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Missing native dependency: $requiredPath. Run scripts\native-dev\install.ps1 first."
    }
}

Import-DotEnv -Path $script:EnvFile

& (Join-Path $PSScriptRoot 'stop.ps1')

Write-Host 'Stopping Docker application containers...'
Invoke-DockerCompose -Arguments @(
    'stop',
    'tgo-web',
    'tgo-api',
    'tgo-ai',
    'tgo-rag',
    'tgo-rag-worker',
    'tgo-rag-beat',
    'tgo-platform',
    'tgo-workflow',
    'tgo-workflow-worker',
    'tgo-plugin-runtime',
    'tgo-device-control',
    'tgo-widget-js'
) -AllowFailure

Write-Host 'Starting minimal Docker infrastructure...'
Invoke-DockerCompose -Arguments @('up', '-d', 'postgres', 'redis', 'wukongim')

$databasePort = [int](Get-EnvValue -Name 'NATIVE_POSTGRES_PORT' -DefaultValue '15432')
$redisPort = [int](Get-EnvValue -Name 'REDIS_PORT' -DefaultValue '6379')
try {
    Wait-TcpPort -Port $databasePort -Label 'PostgreSQL' -TimeoutSeconds 15
} catch {
    Write-Host 'PostgreSQL host port is missing; recreating the container without deleting its data volume...'
    Invoke-DockerCompose -Arguments @('up', '-d', '--force-recreate', 'postgres')
    Wait-TcpPort -Port $databasePort -Label 'PostgreSQL' -TimeoutSeconds 90
}
Wait-TcpPort -Port $redisPort -Label 'Redis' -TimeoutSeconds 60
Wait-TcpPort -Port 5001 -Label 'WuKongIM API' -TimeoutSeconds 90
Wait-TcpPort -Port 5200 -Label 'WuKongIM WebSocket' -TimeoutSeconds 90

if (-not $SkipMigrations) {
    Write-Host 'Applying tgo-api migrations...'
    Set-NativeEnvironment -Service api
    Push-Location $apiDirectory
    try {
        & $apiPython -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw 'tgo-api migration failed.'
        }
    } finally {
        Pop-Location
    }

    Write-Host 'Applying tgo-ai migrations...'
    Set-NativeEnvironment -Service ai
    Push-Location $aiDirectory
    try {
        & $aiPython -m alembic upgrade heads
        if ($LASTEXITCODE -ne 0) {
            throw 'tgo-ai migration failed.'
        }
    } finally {
        Pop-Location
    }

    Write-Host 'Applying tgo-rag migrations...'
    Set-NativeEnvironment -Service rag
    Push-Location $ragDirectory
    try {
        & $ragPython -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw 'tgo-rag migration failed.'
        }
    } finally {
        Pop-Location
    }

    Write-Host 'Applying tgo-workflow migrations...'
    Set-NativeEnvironment -Service workflow
    Push-Location $workflowDirectory
    try {
        & $workflowPython -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) {
            throw 'tgo-workflow migration failed.'
        }
    } finally {
        Pop-Location
    }
}

Assert-PortAvailable -Port 18000 -Label 'tgo-api'
Assert-PortAvailable -Port 18001 -Label 'tgo-api internal service'
Assert-PortAvailable -Port 8081 -Label 'tgo-ai'
Assert-PortAvailable -Port 18082 -Label 'tgo-rag'
Assert-PortAvailable -Port 8004 -Label 'tgo-workflow'
Assert-PortAvailable -Port 5173 -Label 'tgo-web'
Assert-PortAvailable -Port 5174 -Label 'tgo-widget-js'

$processes = @()

Set-NativeEnvironment -Service api
$processes += Start-NativeProcess `
    -Name 'tgo-api' `
    -FilePath $apiPython `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '18000') `
    -WorkingDirectory $apiDirectory
Save-ProcessState -Processes $processes

$processes += Start-NativeProcess `
    -Name 'tgo-api-internal' `
    -FilePath $apiPython `
    -ArgumentList @('-m', 'uvicorn', 'app.internal:internal_app', '--host', '127.0.0.1', '--port', '18001') `
    -WorkingDirectory $apiDirectory
Save-ProcessState -Processes $processes

Set-NativeEnvironment -Service ai
$processes += Start-NativeProcess `
    -Name 'tgo-ai' `
    -FilePath $aiPython `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8081') `
    -WorkingDirectory $aiDirectory
Save-ProcessState -Processes $processes

Set-NativeEnvironment -Service rag
$processes += Start-NativeProcess `
    -Name 'tgo-rag' `
    -FilePath $ragPython `
    -ArgumentList @('-m', 'uvicorn', 'src.rag_service.main:app', '--host', '127.0.0.1', '--port', '18082') `
    -WorkingDirectory $ragDirectory
Save-ProcessState -Processes $processes

if (-not $SkipRagWorker) {
    $processes += Start-NativeProcess `
        -Name 'tgo-rag-worker' `
        -FilePath $ragPython `
        -ArgumentList @(
            '-m',
            'celery',
            '-A',
            'src.rag_service.tasks.celery_app',
            'worker',
            '--pool=solo',
            '--loglevel=info',
            '--hostname=worker@tgo-rag-native',
            '-Q',
            'document_processing,embedding,website_crawling,qa_processing,celery'
        ) `
        -WorkingDirectory $ragDirectory
    Save-ProcessState -Processes $processes
}

Set-NativeEnvironment -Service workflow
$processes += Start-NativeProcess `
    -Name 'tgo-workflow' `
    -FilePath $workflowPython `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8004') `
    -WorkingDirectory $workflowDirectory
Save-ProcessState -Processes $processes

Set-NativeEnvironment -Service web
$processes += Start-NativeProcess `
    -Name 'tgo-web' `
    -FilePath $node `
    -ArgumentList @('node_modules\vite\bin\vite.js', '--host', '127.0.0.1', '--port', '5173') `
    -WorkingDirectory $webDirectory
Save-ProcessState -Processes $processes

Set-NativeEnvironment -Service widget
$processes += Start-NativeProcess `
    -Name 'tgo-widget-js' `
    -FilePath $node `
    -ArgumentList @('node_modules\vite\bin\vite.js', '--host', '127.0.0.1', '--port', '5174') `
    -WorkingDirectory $widgetDirectory
Save-ProcessState -Processes $processes

Wait-Http -Url 'http://127.0.0.1:18000/health' -Label 'tgo-api'
Wait-Http -Url 'http://127.0.0.1:18001/health' -Label 'tgo-api internal service'
Wait-Http -Url 'http://127.0.0.1:8081/health' -Label 'tgo-ai' -TimeoutSeconds 240
Wait-Http -Url 'http://127.0.0.1:18082/health' -Label 'tgo-rag' -TimeoutSeconds 240
Wait-Http -Url 'http://127.0.0.1:8004/health' -Label 'tgo-workflow'
Wait-Http -Url 'http://127.0.0.1:5173/chat' -Label 'tgo-web'
Wait-Http -Url 'http://127.0.0.1:5173/api/v1/setup/status' -Label 'tgo-web API proxy'
Wait-Http -Url 'http://127.0.0.1:5174/' -Label 'tgo-widget-js'

Write-Host ''
Write-Host 'Hybrid native development stack is ready:'
Write-Host '  Admin UI:     http://127.0.0.1:5173/chat'
Write-Host '  TGO API:      http://127.0.0.1:18000'
Write-Host '  TGO Internal: http://127.0.0.1:18001'
Write-Host '  TGO AI:       http://127.0.0.1:8081'
Write-Host '  TGO RAG:      http://127.0.0.1:18082'
Write-Host '  Workflow:     http://127.0.0.1:8004'
Write-Host '  Visitor UI:   http://127.0.0.1:5174'
Write-Host "  Logs:         $script:RuntimeDir"
