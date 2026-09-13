. (Join-Path $PSScriptRoot 'common.ps1')
. (Join-Path $PSScriptRoot 'background-services.ps1')

Import-DotEnv -Path $script:EnvFile
$webPort = [int](Get-EnvValue -Name 'TGO_WEB_PORT' -DefaultValue '5173')

Write-Host 'Native processes:'
if (Test-Path -LiteralPath $script:StateFile) {
    $state = @(Read-ProcessState)
    foreach ($entry in @($state)) {
        $status = if (Test-NativeProcessIdentity -Entry $entry) { 'running' } else { 'stopped or stale' }
        Write-Host ("  {0,-18} PID={1,-7} {2}" -f $entry.name, $entry.pid, $status)
    }
} else {
    Write-Host '  No native process state file found.'
}

Write-Host ''
Write-Host 'Endpoints:'
foreach ($endpoint in @(
    @{ Name = 'tgo-web'; Url = "http://127.0.0.1:$webPort/chat" },
    @{ Name = 'tgo-api'; Url = 'http://127.0.0.1:18000/health' },
    @{ Name = 'tgo-api-internal'; Url = 'http://127.0.0.1:18001/health' },
    @{ Name = 'tgo-ai'; Url = 'http://127.0.0.1:8081/health' },
    @{ Name = 'tgo-rag'; Url = 'http://127.0.0.1:18082/health' },
    @{ Name = 'tgo-platform'; Url = 'http://127.0.0.1:8003/health' },
    @{ Name = 'tgo-workflow'; Url = 'http://127.0.0.1:8004/health' },
    @{ Name = 'tgo-plugin-runtime'; Url = 'http://127.0.0.1:8090/health' },
    @{ Name = 'tgo-device-control'; Url = 'http://127.0.0.1:8085/health' },
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
