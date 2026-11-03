$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
& (Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'tests/workflow-parallel-control-live.py') --run-local
if($LASTEXITCODE -ne 0){throw 'Parallel runtime verification failed; inspect the same run report and cleanup state.'}
Write-Output 'PASS: real parallel HTTP overlap, failure, timeout and three cancellation paths; synthetic AI only.'
