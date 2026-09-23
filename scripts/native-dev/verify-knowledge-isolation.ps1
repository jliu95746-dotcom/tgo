# Run with powershell.exe -NoProfile -ExecutionPolicy Bypass -File <this file>.
# Uses private transaction schemas and temporary loopback HTTP servers.
# Customer records, feature flags and existing service processes are untouched.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

Set-NativeEnvironment -Service api
$env:API_KNOWLEDGE_TEST_DATABASE_URL = $env:DATABASE_URL
Set-NativeEnvironment -Service rag
$env:SAAS_TEST_DATABASE_URL = $env:DATABASE_URL

$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-rag\.venv\Scripts\python.exe'
$verificationScript = Join-Path $PSScriptRoot 'tests\knowledge-http-rag.py'
if (-not (Test-Path -LiteralPath $verificationPython)) {
    throw 'RAG Python environment is missing; install native dependencies first.'
}
& $verificationPython $verificationScript
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: two-company knowledge HTTP isolation verified; temporary schemas rolled back.'
