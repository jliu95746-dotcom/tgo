# Real loopback APIs, Redis and Workers; owned temporary fixtures only.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service workflow
& (Join-Path $script:RepoRoot 'repos/tgo-workflow/.venv/Scripts/python.exe') (Join-Path $PSScriptRoot 'tests/workflow-approval-keys-live.py') --run-local
if ($LASTEXITCODE -ne 0) { throw 'Approval key verification failed; inspect its report and cleanup state.' }
Write-Output 'PASS: approval keys across real APIs and Workers, retained cursor and revocation.'
