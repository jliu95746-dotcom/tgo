param(
    [switch]$SkipRagWorker, [switch]$SkipMigrations,
    [ValidateSet('tgo-rag-worker', 'tgo-workflow-worker', 'tgo-plugin-runtime')]
    [string[]]$ServiceNames = @('tgo-rag-worker', 'tgo-workflow-worker', 'tgo-plugin-runtime')
)

. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'background-services.ps1')
Import-DotEnv -Path $script:EnvFile
Start-NativeBackgroundServices -SkipRagWorker:$SkipRagWorker -SkipMigrations:$SkipMigrations -ServiceNames $ServiceNames
Write-Host 'Selected native background services answered readiness checks.'
