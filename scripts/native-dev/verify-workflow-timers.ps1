# Creates its own database, queue, HTTP servers, Worker and Beat.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
$workflowPython = Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
$apiPython = Join-Path $script:RepoRoot 'repos/tgo-api/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $workflowPython) -or -not (Test-Path -LiteralPath $apiPython)) {
    throw 'Native Workflow and API dependencies are required.'
}
Ensure-RuntimeDirectory
& $workflowPython (Join-Path $PSScriptRoot 'tests/workflow-timers-live.py') --run-local
if ($LASTEXITCODE -ne 0) {
    throw 'Timer runtime verification failed; inspect its report and cleanup state.'
}
Write-Output 'PASS: private HTTP authorization, Redis, Beat and Worker timer verification.'
