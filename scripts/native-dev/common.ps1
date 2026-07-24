Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:RuntimeDir = Join-Path $script:RepoRoot '.tmp\native-dev'
$script:StateFile = Join-Path $script:RuntimeDir 'processes.json'
$script:EnvFile = Join-Path $script:RepoRoot '.env.dev'
$script:ComposeFiles = @(
    (Join-Path $script:RepoRoot 'docker-compose.yml'),
    (Join-Path $script:RepoRoot 'docker-compose.dev.yml'),
    (Join-Path $script:RepoRoot 'docker-compose.native.yml')
)

function Ensure-RuntimeDirectory {
    if (-not (Test-Path -LiteralPath $script:RuntimeDir)) {
        New-Item -ItemType Directory -Path $script:RuntimeDir -Force | Out-Null
    }
}

function Import-DotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Environment file not found: $Path"
    }

    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#')) {
            continue
        }
        if ($line.StartsWith('export ')) {
            $line = $line.Substring(7).Trim()
        }

        $separator = $line.IndexOf('=')
        if ($separator -le 0) {
            continue
        }

        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
            continue
        }

        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [Environment]::SetEnvironmentVariable($name, $value, 'Process')
    }
}

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$DefaultValue
    )

    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $DefaultValue
    }
    return $value
}

function Set-ProcessEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

function Set-NativeEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('api', 'ai', 'rag', 'workflow', 'web', 'widget')]
        [string]$Service
    )

    Import-DotEnv -Path $script:EnvFile

    $databaseUser = [Uri]::EscapeDataString((Get-EnvValue -Name 'POSTGRES_USER' -DefaultValue 'tgo'))
    $databasePassword = [Uri]::EscapeDataString((Get-EnvValue -Name 'POSTGRES_PASSWORD' -DefaultValue 'tgo'))
    $databaseName = [Uri]::EscapeDataString((Get-EnvValue -Name 'POSTGRES_DB' -DefaultValue 'tgo'))
    $databasePort = Get-EnvValue -Name 'NATIVE_POSTGRES_PORT' -DefaultValue '15432'
    $redisPort = Get-EnvValue -Name 'REDIS_PORT' -DefaultValue '6379'
    $databaseUrl = "postgresql+asyncpg://${databaseUser}:${databasePassword}@127.0.0.1:${databasePort}/${databaseName}"

    Set-ProcessEnv -Name 'ENVIRONMENT' -Value 'development'
    Set-ProcessEnv -Name 'DATABASE_URL' -Value $databaseUrl
    Set-ProcessEnv -Name 'POSTGRES_HOST' -Value '127.0.0.1'
    Set-ProcessEnv -Name 'POSTGRES_PORT' -Value $databasePort
    Set-ProcessEnv -Name 'REDIS_HOST' -Value '127.0.0.1'
    Set-ProcessEnv -Name 'PYTHONUTF8' -Value '1'
    Set-ProcessEnv -Name 'PYTHONIOENCODING' -Value 'utf-8'

    if ($Service -eq 'api') {
        Set-ProcessEnv -Name 'REDIS_URL' -Value "redis://127.0.0.1:${redisPort}/0"
        Set-ProcessEnv -Name 'API_BASE_URL' -Value 'http://127.0.0.1:18000'
        Set-ProcessEnv -Name 'INTERNAL_SERVICE_HOST' -Value '127.0.0.1'
        Set-ProcessEnv -Name 'INTERNAL_SERVICE_PORT' -Value '18001'
        Set-ProcessEnv -Name 'AI_SERVICE_URL' -Value 'http://127.0.0.1:8081'
        Set-ProcessEnv -Name 'RAG_SERVICE_URL' -Value 'http://127.0.0.1:18082'
        Set-ProcessEnv -Name 'WORKFLOW_SERVICE_URL' -Value 'http://127.0.0.1:8004'
        Set-ProcessEnv -Name 'PLATFORM_SERVICE_URL' -Value 'http://127.0.0.1:8003'
        Set-ProcessEnv -Name 'PLUGIN_RUNTIME_URL' -Value 'http://127.0.0.1:8090'
        Set-ProcessEnv -Name 'DEVICE_CONTROL_SERVICE_URL' -Value 'http://127.0.0.1:8085'
        Set-ProcessEnv -Name 'WUKONGIM_SERVICE_URL' -Value 'http://127.0.0.1:5001'
        Set-ProcessEnv -Name 'WUKONGIM_ENABLED' -Value 'true'
    }

    if ($Service -eq 'ai') {
        Set-ProcessEnv -Name 'REDIS_URL' -Value "redis://127.0.0.1:${redisPort}/1"
        Set-ProcessEnv -Name 'HOST' -Value '127.0.0.1'
        Set-ProcessEnv -Name 'PORT' -Value '8081'
        Set-ProcessEnv -Name 'RELOAD' -Value 'false'
        Set-ProcessEnv -Name 'API_SERVICE_URL' -Value 'http://127.0.0.1:18000'
        Set-ProcessEnv -Name 'API_INTERNAL_SERVICE_URL' -Value 'http://127.0.0.1:18001'
        Set-ProcessEnv -Name 'RAG_SERVICE_URL' -Value 'http://127.0.0.1:18082'
        Set-ProcessEnv -Name 'WORKFLOW_SERVICE_URL' -Value 'http://127.0.0.1:8004'
        Set-ProcessEnv -Name 'PLUGIN_RUNTIME_URL' -Value 'http://127.0.0.1:8090'
        Set-ProcessEnv -Name 'MCP_SERVICE_URL' -Value 'http://127.0.0.1:8090'
        Set-ProcessEnv -Name 'DEVICE_CONTROL_MCP_ENDPOINT' -Value 'http://127.0.0.1:8085/mcp/{device_id}'
        Set-ProcessEnv -Name 'SKILLS_BASE_DIR' -Value (Join-Path $script:RepoRoot 'data\skills')
    }

    if ($Service -eq 'rag') {
        $ragUploadDirectory = Join-Path $script:RepoRoot 'data\tgo-rag\uploads'
        Set-ProcessEnv -Name 'REDIS_URL' -Value "redis://127.0.0.1:${redisPort}/2"
        Set-ProcessEnv -Name 'REDIS_DB' -Value '2'
        Set-ProcessEnv -Name 'CELERY_BROKER_URL' -Value "redis://127.0.0.1:${redisPort}/2"
        Set-ProcessEnv -Name 'CELERY_RESULT_BACKEND' -Value "redis://127.0.0.1:${redisPort}/2"
        Set-ProcessEnv -Name 'HOST' -Value '127.0.0.1'
        Set-ProcessEnv -Name 'PORT' -Value '18082'
        Set-ProcessEnv -Name 'RELOAD' -Value 'false'
        Set-ProcessEnv -Name 'UPLOAD_DIR' -Value $ragUploadDirectory
    }

    if ($Service -eq 'workflow') {
        Set-ProcessEnv -Name 'REDIS_URL' -Value "redis://127.0.0.1:${redisPort}/3"
        Set-ProcessEnv -Name 'AI_SERVICE_URL' -Value 'http://127.0.0.1:8081'
        Set-ProcessEnv -Name 'ALLOWED_ORIGINS' -Value '["*"]'
    }

    if ($Service -eq 'web') {
        Set-ProcessEnv -Name 'VITE_API_BASE_URL' -Value '/api'
        Set-ProcessEnv -Name 'VITE_API_PROXY_TARGET' -Value 'http://127.0.0.1:18000'
        Set-ProcessEnv -Name 'CHOKIDAR_USEPOLLING' -Value 'false'
    }

    if ($Service -eq 'widget') {
        Set-ProcessEnv -Name 'VITE_API_BASE_URL' -Value 'http://127.0.0.1:18000'
        Set-ProcessEnv -Name 'CHOKIDAR_USEPOLLING' -Value 'false'
    }
}

function Get-ComposeArguments {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Tail
    )

    $arguments = @('compose', '--env-file', $script:EnvFile)
    foreach ($composeFile in $script:ComposeFiles) {
        $arguments += @('-f', $composeFile)
    }
    return $arguments + $Tail
}

function Invoke-DockerCompose {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )

    $docker = (Get-Command docker -ErrorAction Stop).Source
    & $docker (Get-ComposeArguments -Tail $Arguments)
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "docker compose failed with exit code $LASTEXITCODE"
    }
}

function Wait-TcpPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [string]$Label = "port $Port",
        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync('127.0.0.1', $Port)
            if ($task.Wait(1000) -and $client.Connected) {
                return
            }
        } catch {
        } finally {
            $client.Dispose()
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Label on port $Port"
}

function Wait-Http {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,
        [string]$Label = $Url,
        [int]$TimeoutSeconds = 90
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return
            }
        } catch {
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Label at $Url"
}

function Assert-PortAvailable {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    $listener = netstat -ano | Select-String "LISTENING\s+\d+$" | Select-String ":$Port\s"
    if ($listener) {
        throw "$Label cannot start because port $Port is already in use."
    }
}

function Start-NativeProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    Ensure-RuntimeDirectory
    $stdout = Join-Path $script:RuntimeDir "$Name.out.log"
    $stderr = Join-Path $script:RuntimeDir "$Name.err.log"

    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru `
        -WindowStyle Hidden

    return [pscustomobject]@{
        name = $Name
        pid = $process.Id
        executable = $FilePath
        workingDirectory = $WorkingDirectory
        startedAt = (Get-Date).ToString('o')
    }
}

function Save-ProcessState {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Processes
    )

    Ensure-RuntimeDirectory
    $Processes | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $script:StateFile -Encoding UTF8
}
