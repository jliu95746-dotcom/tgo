# Dedicated Redis queue and private PostgreSQL schema; no external model calls.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service rag
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-rag\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\qa-worker-isolation-e2e.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: Redis/Celery QA worker isolation verified with synthetic embeddings.'
