. (Join-Path $PSScriptRoot 'common.ps1')

Write-Host 'Native processes:'
if (Test-Path -LiteralPath $script:StateFile) {
    $state = Get-Content -Raw -LiteralPath $script:StateFile | ConvertFrom-Json
    foreach ($entry in @($state)) {
        $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
        $status = if ($null -eq $process) { 'stopped' } else { 'running' }
        Write-Host ("  {0,-18} PID={1,-7} {2}" -f $entry.name, $entry.pid, $status)
    }
} else {
    Write-Host '  No native process state file found.'
}

Write-Host ''
Write-Host 'Endpoints:'
foreach ($endpoint in @(
    @{ Name = 'tgo-web'; Url = 'http://127.0.0.1:5173/chat' },
    @{ Name = 'tgo-api'; Url = 'http://127.0.0.1:18000/health' },
    @{ Name = 'tgo-api-internal'; Url = 'http://127.0.0.1:18001/health' },
    @{ Name = 'tgo-ai'; Url = 'http://127.0.0.1:8081/health' },
    @{ Name = 'tgo-rag'; Url = 'http://127.0.0.1:18082/health' },
    @{ Name = 'tgo-workflow'; Url = 'http://127.0.0.1:8004/health' },
    @{ Name = 'tgo-widget-js'; Url = 'http://127.0.0.1:5174/' }
)) {
    try {
        $response = Invoke-WebRequest -Uri $endpoint.Url -UseBasicParsing -TimeoutSec 3
        Write-Host ("  {0,-18} HTTP {1}" -f $endpoint.Name, $response.StatusCode)
    } catch {
        Write-Host ("  {0,-18} unavailable" -f $endpoint.Name)
    }
}

Write-Host ''
Write-Host 'Docker infrastructure:'
Invoke-DockerCompose -Arguments @('ps', 'postgres', 'redis', 'wukongim') -AllowFailure
