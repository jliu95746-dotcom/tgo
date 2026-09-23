# Real local HTTP crawl, private SQL and Redis queue; test embeddings only.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service rag
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-rag\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\website-pipeline-isolation-e2e.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: two-company website crawl and search isolation verified.'
