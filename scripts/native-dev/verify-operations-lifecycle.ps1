# Real HTTP and PostgreSQL; migrations run only inside a rolled-back schema.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service api
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-api\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\operations-lifecycle-isolation.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: operator lifecycle and company migration preview verified.'
