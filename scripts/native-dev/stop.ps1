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

        $recordedExecutable = [System.IO.Path]::GetFullPath([string]$entry.executable)
        $runningExecutable = $process.Path
        $executableMatches = (
            -not [string]::IsNullOrWhiteSpace($runningExecutable) -and
            [string]::Equals(
                [System.IO.Path]::GetFullPath($runningExecutable),
                $recordedExecutable,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        )
        $startTimeMatches = $false
        if ($entry.startedAt) {
            $recordedStartTime = [DateTimeOffset]::Parse([string]$entry.startedAt)
            $runningStartTime = [DateTimeOffset]$process.StartTime
            $startTimeDelta = [Math]::Abs(
                ($runningStartTime.ToUniversalTime() - $recordedStartTime.ToUniversalTime()).TotalSeconds
            )
            $startTimeMatches = $startTimeDelta -le 10
        }
        if (-not $executableMatches -and -not $startTimeMatches) {
            Write-Warning "Skipping stale PID $($entry.pid) for $($entry.name); process identity no longer matches."
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
