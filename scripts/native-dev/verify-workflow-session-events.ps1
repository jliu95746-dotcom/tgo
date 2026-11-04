param([switch]$VerifyTimerManagement)
# Uses an owned local tenant and real IM/Redis/Worker; no external model calls.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'background-services.ps1')

Set-NativeEnvironment -Service api
$python = Join-Path $script:RepoRoot 'repos/tgo-api/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Native API dependencies are missing.' }
foreach ($endpoint in @(
    ($env:API_BASE_URL.TrimEnd('/') + '/health'),
    ($env:WORKFLOW_SERVICE_URL.TrimEnd('/') + '/health'),
    ('http://{0}:{1}/health' -f $env:INTERNAL_SERVICE_HOST, $env:INTERNAL_SERVICE_PORT)
)) {
    $uri = [Uri]$endpoint
    if (-not $uri.IsLoopback -or $uri.Scheme -ne 'http') { throw 'Only loopback HTTP services are allowed.' }
    $response = Invoke-WebRequest -Uri $endpoint -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -ne 200) { throw 'A required local service is unavailable.' }
}

# An inspect ping is read-only and does not enqueue a task.
$workflowPython = Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
Set-NativeEnvironment -Service workflow
if (-not (Test-NativeWorkerReady -Python $workflowPython -Node 'worker@tgo-workflow-native')) {
    throw 'The native workflow worker is not ready. No test fixture was created.'
}
Set-NativeEnvironment -Service api
Ensure-RuntimeDirectory
$verificationArguments = @('--run-local')
if ($VerifyTimerManagement) { $verificationArguments += '--verify-timer-management' }
& $python (Join-Path $PSScriptRoot 'tests/workflow-session-events-live.py') @verificationArguments
if ($LASTEXITCODE -ne 0) { throw 'Session source event verification failed; inspect the local report and cleanup status.' }
Write-Output 'PASS: authenticated session-close event, actual IM excerpt and real workflow worker.'
