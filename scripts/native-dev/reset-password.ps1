[CmdletBinding()]
param([switch]$Help)

. (Join-Path $PSScriptRoot 'common.ps1')
Set-ProcessEnv -Name 'PYTHONUTF8' -Value '1'
Set-ProcessEnv -Name 'PYTHONIOENCODING' -Value 'utf-8'

$taskPython = Join-Path $script:RepoRoot 'repos\tgo-api\.venv\Scripts\python.exe'
$taskRecovery = Join-Path $script:RepoRoot 'repos\tgo-api\scripts\reset_staff_password.py'
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw '缺少 API 本机 Python 环境，请先运行混合环境安装流程。'
}
if ($Help) {
    & $taskPython $taskRecovery --help
    exit $LASTEXITCODE
}

Set-NativeEnvironment -Service api
& $taskPython $taskRecovery
exit $LASTEXITCODE
