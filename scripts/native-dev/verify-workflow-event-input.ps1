# Uses a disposable local database and queue; actual private keys stay unchanged.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
$python=Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
& $python (Join-Path $PSScriptRoot 'tests/workflow-event-input-live.py') --run-local
if ($LASTEXITCODE -ne 0) { throw 'Event input runtime verification failed; inspect the current report and cleanup state.' }
Write-Output 'PASS: event input encryption, independent key rotation and real queue recovery.'
