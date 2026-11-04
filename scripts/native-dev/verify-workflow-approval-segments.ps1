# Uses only a disposable loopback database, HTTP listeners and a private queue.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
$python=Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe'
& $python (Join-Path $PSScriptRoot 'tests/workflow-approval-segments-live.py') --run-local
if ($LASTEXITCODE -ne 0) { throw 'Approval segment runtime verification failed; inspect the current report and cleanup state.' }
Write-Output 'PASS: long approval waits, actual queue resumes and duplicate deliveries.'
