$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../common.ps1')

# Exercise public environment derivation without reading private credentials.
$script:qaRedisEnvironment = @{}
function Import-DotEnv { param([string]$Path) }
function Get-EnvValue {
    param([string]$Name, [string]$DefaultValue)
    if ($script:qaRedisEnvironment.ContainsKey($Name) -and
        -not [string]::IsNullOrWhiteSpace($script:qaRedisEnvironment[$Name])) {
        return [string]$script:qaRedisEnvironment[$Name]
    }
    return $DefaultValue
}
function Set-ProcessEnv {
    param([string]$Name, [string]$Value)
    $script:qaRedisEnvironment[$Name] = $Value
}
function Assert-Equal($Actual, $Expected, [string]$Message) {
    if ($Actual -ne $Expected) { throw $Message }
}

$checks = 0
foreach ($case in @(
    @{ Service = 'api'; Database = 0 },
    @{ Service = 'ai'; Database = 1 },
    @{ Service = 'rag'; Database = 2 },
    @{ Service = 'platform'; Database = 0 },
    @{ Service = 'workflow'; Database = 3 },
    @{ Service = 'device'; Database = 3 }
)) {
    $script:qaRedisEnvironment = @{ REDIS_PORT = '6379'; NATIVE_REDIS_PORT = '16379' }
    Set-NativeEnvironment -Service $case.Service
    Assert-Equal $script:qaRedisEnvironment['REDIS_URL'] "redis://127.0.0.1:16379/$($case.Database)" 'Native service must use the host Redis port and its own logical database'
    Assert-Equal $script:qaRedisEnvironment['REDIS_PORT'] '6379' 'Native host override must preserve the container Redis port'
    $checks += 2
    if ($case.Service -in @('rag', 'workflow')) {
        Assert-Equal $script:qaRedisEnvironment['CELERY_BROKER_URL'] $script:qaRedisEnvironment['REDIS_URL'] 'Celery broker must use the same host port'
        Assert-Equal $script:qaRedisEnvironment['CELERY_RESULT_BACKEND'] $script:qaRedisEnvironment['REDIS_URL'] 'Celery results must use the same host port'
        $checks += 2
    }
}

$script:qaRedisEnvironment = @{ REDIS_PORT = '16380' }
Assert-Equal (Get-NativeRedisPort) 16380 'Legacy REDIS_PORT must remain supported'
$script:qaRedisEnvironment = @{}
Assert-Equal (Get-NativeRedisPort) 6379 'An absent override must retain the legacy default'
$script:qaRedisEnvironment = @{ NATIVE_REDIS_PORT = ''; REDIS_PORT = '16380' }
Assert-Equal (Get-NativeRedisPort) 16380 'An empty native override must use the configured fallback'
$checks += 3

foreach ($invalid in @('0', '65536', 'not-a-port', '-1')) {
    $script:qaRedisEnvironment = @{ NATIVE_REDIS_PORT = $invalid }
    $rejected = $false
    try { Get-NativeRedisPort | Out-Null } catch { $rejected = $true }
    Assert-Equal $rejected $true 'Invalid Redis host ports must be rejected'
    $checks += 1
}

Write-Output "Native Redis environment: $checks checks passed"
