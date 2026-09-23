# Real HTTP and IM state; business records live in a rolled-back SQL schema.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service api
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-api\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\conversation-im-isolation.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: real IM conversation history, unread and deletion isolation verified.'
