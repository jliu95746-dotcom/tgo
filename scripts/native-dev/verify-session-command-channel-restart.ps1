# A separate process recovers committed synthetic effects against local WuKongIM.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service api
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-api\.venv\Scripts\python.exe'
& $verificationPython (Join-Path $PSScriptRoot 'tests\session-command-channel-restart.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Output 'PASS: real IM effects recovered after process boundary.'
