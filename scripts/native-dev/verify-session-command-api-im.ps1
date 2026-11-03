# Four staff session routes on synthetic records, using the real local IM API.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-NativeEnvironment -Service api
$env:TGO_REAL_IM_TEST = '1'
$verificationPython = Join-Path $script:RepoRoot 'repos\tgo-api\.venv\Scripts\python.exe'
Push-Location (Join-Path $script:RepoRoot 'repos\tgo-api')
try {
    & $verificationPython -m pytest -q -o addopts= --disable-warnings tests/test_staff_session_commands_real_im.py
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}
Write-Output 'PASS: four session routes and real local IM effects verified.'
