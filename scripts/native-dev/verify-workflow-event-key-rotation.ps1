param([ValidateRange(0, 65535)][int]$Port = 0)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'background-services.ps1')

Set-NativeEnvironment -Service workflow
$python = Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Native Workflow dependencies are missing.' }
if (-not (Test-NativeWorkerReady -Python $python -Node 'worker@tgo-workflow-native')) {
    throw 'The native workflow worker is not ready. No fixture was created.'
}
Ensure-RuntimeDirectory
& $python (Join-Path $PSScriptRoot 'tests/workflow-event-key-rotation-live.py') --run-local --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Receipt-key rotation verification failed; inspect its local report.' }
Write-Output 'PASS: ephemeral receipt keys rotate across actual Workflow processes.'
