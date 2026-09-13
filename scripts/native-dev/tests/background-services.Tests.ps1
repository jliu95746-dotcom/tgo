$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\common.ps1')

function Assert-Equal($Actual, $Expected, [string]$Message) {
    if ($Actual -ne $Expected) {
        throw "$Message (expected: $Expected; actual: $Actual)"
    }
}

# Switching from RAG to workflow must not leave Celery's own overrides on DB 2.
Set-NativeEnvironment -Service rag
Set-NativeEnvironment -Service workflow
Assert-Equal $env:CELERY_BROKER_URL $env:REDIS_URL 'Workflow broker must match its Redis database'
Assert-Equal $env:CELERY_RESULT_BACKEND $env:REDIS_URL 'Workflow result backend must match its Redis database'

Set-NativeEnvironment -Service plugin
Assert-Equal $env:PLUGIN_SOCKET_ENABLED 'false' 'Native plugins must not start a Unix socket'
Assert-Equal $env:PLUGIN_TCP_HOST '127.0.0.1' 'Native plugin transport must stay on loopback'
Assert-Equal $env:PLUGIN_BASE_PATH (Join-Path $script:RepoRoot 'data\plugins') 'Plugin files must stay in the workspace'

. (Join-Path $PSScriptRoot '..\background-services.ps1')
$testProcess = Get-Process -Id $PID
$testEntry = [pscustomobject]@{
    pid = $PID; executable = $testProcess.Path; startedAt = $testProcess.StartTime
}
Assert-Equal (Test-NativeProcessIdentity -Entry $testEntry) $true 'JSON-parsed DateTime values must keep their date and timezone'
$testEntry.startedAt = $testProcess.StartTime.AddHours(-1)
Assert-Equal (Test-NativeProcessIdentity -Entry $testEntry) $false 'Stale process records must be rejected'
$definitions = @(Get-NativeBackgroundServices)
Assert-Equal $definitions.Count 3 'All required background services must be defined'
$workflow = $definitions | Where-Object { $_.Name -eq 'tgo-workflow-worker' }
Assert-Equal ($workflow.Arguments -contains '--pool=solo') $true 'Workflow worker must support Windows'
Assert-Equal ($workflow.Arguments -contains 'workflow') $true 'Workflow worker must consume the workflow queue'
$rag = $definitions | Where-Object { $_.Name -eq 'tgo-rag-worker' }
Assert-Equal ($rag.Arguments -contains 'document_processing,embedding,website_crawling,qa_processing,celery') $true 'RAG worker must consume every processing queue'
$plugin = $definitions | Where-Object { $_.Name -eq 'tgo-plugin-runtime' }
Assert-Equal ($plugin.Ports -contains [int]$env:PLUGIN_TCP_PORT) $true 'Plugin transport must be checked before startup'

Write-Host 'PASS: native background environment and service definitions'
