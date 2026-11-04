# Owned loopback database, API, queue and processes only; no native data mutation.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
$python=Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
& $python (Join-Path $PSScriptRoot 'tests/workflow-event-source-audit-live.py') --run-local
if ($LASTEXITCODE -ne 0) { throw 'Event source audit verification failed; inspect its report and cleanup state.' }
Write-Output 'PASS: real source authority, Redis lease, Beat audit and approval cleanup.'
