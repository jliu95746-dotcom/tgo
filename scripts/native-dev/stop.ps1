param(
    [switch]$IncludeInfrastructure
)

. (Join-Path $PSScriptRoot 'common.ps1')

if (Test-Path -LiteralPath $script:StateFile) {
    $state = Get-Content -Raw -LiteralPath $script:StateFile | ConvertFrom-Json
    foreach ($entry in @($state)) {
        $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            continue
        }

        Write-Host "Stopping $($entry.name) (PID $($entry.pid))..."
        & taskkill.exe /PID $entry.pid /T /F | Out-Null
    }
    Remove-Item -LiteralPath $script:StateFile -Force
}

if ($IncludeInfrastructure) {
    Write-Host 'Stopping Docker infrastructure...'
    Invoke-DockerCompose -Arguments @('stop', 'postgres', 'redis', 'wukongim')
}

Write-Host 'Native application services are stopped.'
