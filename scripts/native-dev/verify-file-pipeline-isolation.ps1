# Real text parser, dedicated Redis queue and pgvector; test embedding model.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service rag
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-rag\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\file-pipeline-isolation-e2e.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: two-company file processing and pgvector retrieval isolation verified.'
