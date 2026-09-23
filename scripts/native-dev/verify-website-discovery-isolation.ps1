# Real recursive task dispatch and concurrent private SQL page reservations.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service rag
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-rag\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\website-discovery-isolation-e2e.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: website discovery tenant boundaries and concurrent page limits verified.'
