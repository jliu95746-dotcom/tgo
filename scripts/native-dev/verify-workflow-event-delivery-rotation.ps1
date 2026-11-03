$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'background-services.ps1')

Set-NativeEnvironment -Service workflow
$workflowPython = Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
if (-not (Test-NativeWorkerReady -Python $workflowPython -Node 'worker@tgo-workflow-native')) {
    throw 'Native Workflow Worker is unavailable; no test fixture was created.'
}
Set-NativeEnvironment -Service api
$python = Join-Path $script:RepoRoot 'repos/tgo-api/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Native API dependencies are missing.' }
foreach ($endpoint in @(
    ($env:API_BASE_URL.TrimEnd('/') + '/health'),
    ($env:WORKFLOW_SERVICE_URL.TrimEnd('/') + '/health'),
    ('http://{0}:{1}/health' -f $env:INTERNAL_SERVICE_HOST, $env:INTERNAL_SERVICE_PORT)
)) {
    $uri = [Uri]$endpoint
    if (-not $uri.IsLoopback -or $uri.Scheme -ne 'http') { throw 'Only loopback HTTP is permitted.' }
    $response = Invoke-WebRequest -Uri $endpoint -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -ne 200) { throw 'A required local service is unavailable.' }
}
Ensure-RuntimeDirectory
& $python (Join-Path $PSScriptRoot 'tests/workflow-event-delivery-rotation-live.py') --run-local
if ($LASTEXITCODE -ne 0) { throw 'Frozen delivery rotation verification failed; inspect the local report.' }
Write-Output 'PASS: frozen source delivery survives acknowledgement loss and cipher-key rotation.'
