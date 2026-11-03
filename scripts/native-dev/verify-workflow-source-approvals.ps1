# Published source -> real approval -> exact continuation, owned loopback only.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
& (Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'tests/workflow-source-approvals-live.py') --run-local
if ($LASTEXITCODE -ne 0) { throw 'Source approval runtime verification failed; inspect its report and cleanup state.' }
Write-Output 'PASS: published event and Webhook approvals, key recovery, real source revocation and cleanup.'
