param(
    [switch]$SkipMigrations,
    [switch]$SkipRagWorker
)

& (Join-Path $PSScriptRoot 'stop.ps1')
& (Join-Path $PSScriptRoot 'start.ps1') `
    -SkipMigrations:$SkipMigrations `
    -SkipRagWorker:$SkipRagWorker
