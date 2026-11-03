$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../common.ps1')
. (Join-Path $PSScriptRoot '../background-services.ps1')

$services = @(Get-NativeBackgroundServices)
$worker = $services | Where-Object Name -EQ 'tgo-workflow-worker'
if ($worker.Arguments -contains '-Q') {
    throw 'Native Worker must consume the queue configured by the Celery application.'
}
foreach ($filename in @('docker-compose.yml', 'docker-compose.dev.yml')) {
    $source = Get-Content -LiteralPath (Join-Path $script:RepoRoot $filename) -Raw
    if ($source -match 'celery -A celery_app\.celery worker[^\r\n]*-Q workflow') {
        throw "Workflow Worker queue is hardcoded in $filename"
    }
}
Write-Output 'Workflow queue configuration: 3 checks passed'
