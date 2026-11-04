# Real local IM effects on a random channel; SQL stays in a rolled-back schema.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service api
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-api\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\session-command-channel-effects.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: real WuKongIM channel effects verified.'
