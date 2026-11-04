# Runs only disposable PostgreSQL, HTTP, Redis, Worker and Beat fixtures.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
$workflowPython = Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
$apiPython = Join-Path $script:RepoRoot 'repos/tgo-api/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $workflowPython) -or -not (Test-Path -LiteralPath $apiPython)) {
    throw 'Native Workflow and API dependencies are required.'
}
Ensure-RuntimeDirectory
& $workflowPython (Join-Path $PSScriptRoot 'tests/workflow-webhooks-live.py') --run-local
if ($LASTEXITCODE -ne 0) {
    throw 'Webhook verification failed; inspect its fresh report and cleanup state.'
}
Write-Output 'PASS: isolated Webhook PostgreSQL, staff JWT, HTTP, Redis, Beat and Worker checks.'
