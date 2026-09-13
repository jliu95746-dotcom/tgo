function Get-NativeBackgroundServices {
    $pluginTcpPort = [int](Get-EnvValue -Name 'PLUGIN_TCP_PORT' -DefaultValue '8005')
    return @(
        [pscustomobject]@{
            Name = 'tgo-rag-worker'; Environment = 'rag'; Directory = 'repos\tgo-rag'
            Arguments = @('-m', 'celery', '-A', 'src.rag_service.tasks.celery_app', 'worker',
                '--pool=solo', '--loglevel=info', '--hostname=worker@tgo-rag-native',
                '-Q', 'document_processing,embedding,website_crawling,qa_processing,celery')
            Ports = @(); HealthUrl = $null
            WorkerNode = 'worker@tgo-rag-native'
            ConflictingWorkerNodes = @('worker@tgo-rag-worker')
        },
        [pscustomobject]@{
            Name = 'tgo-workflow-worker'; Environment = 'workflow'; Directory = 'repos\tgo-workflow'
            Arguments = @('-m', 'celery', '-A', 'celery_app.celery', 'worker',
                '--pool=solo', '--loglevel=info', '--hostname=worker@tgo-workflow-native', '-Q', 'workflow')
            Ports = @(); HealthUrl = $null
            WorkerNode = 'worker@tgo-workflow-native'
            ConflictingWorkerNodes = @()
        },
        [pscustomobject]@{
            Name = 'tgo-plugin-runtime'; Environment = 'plugin'; Directory = 'repos\tgo-plugin-runtime'
            Arguments = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8090')
            Ports = @(8090, $pluginTcpPort); HealthUrl = 'http://127.0.0.1:8090/health'
            WorkerNode = $null
            ConflictingWorkerNodes = @()
        }
    )
}

function Test-NativeProcessIdentity {
    param([Parameter(Mandatory = $true)][object]$Entry)
    $process = Get-Process -Id $Entry.pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    try {
        $recordedStart = if ($Entry.startedAt -is [DateTime]) {
            [DateTimeOffset]$Entry.startedAt
        } elseif ($Entry.startedAt -is [DateTimeOffset]) {
            $Entry.startedAt
        } else {
            [DateTimeOffset]::Parse([string]$Entry.startedAt, [Globalization.CultureInfo]::InvariantCulture)
        }
        $startedAt = [DateTimeOffset]$process.StartTime
        if ([Math]::Abs(($recordedStart - $startedAt).TotalSeconds) -gt 10) { return $false }
        if ($process.Path -and -not [string]::Equals(
            [System.IO.Path]::GetFullPath($process.Path),
            [System.IO.Path]::GetFullPath([string]$Entry.executable),
            [StringComparison]::OrdinalIgnoreCase
        )) { return $false }
        return $true
    } catch { return $false }
}

function Test-NativeWorkerReady {
    param([string]$Python, [string]$Node)
    $probe = Join-Path $PSScriptRoot 'worker_probe.py'
    $null = & $Python $probe $Node 2>$null
    return $LASTEXITCODE -eq 0
}

function Wait-NativeBackgroundReady {
    param([object]$Definition, [object]$Entry, [string]$Python, [int]$TimeoutSeconds = 60)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (-not (Test-NativeProcessIdentity -Entry $Entry)) {
            throw "$($Definition.Name) exited before readiness. See .tmp/native-dev/$($Definition.Name).err.log"
        }
        if ($Definition.WorkerNode) {
            if (Test-NativeWorkerReady -Python $Python -Node $Definition.WorkerNode) { return }
        } else {
            try {
                $response = Invoke-WebRequest -Uri $Definition.HealthUrl -UseBasicParsing -TimeoutSec 3
                if ($response.StatusCode -eq 200) { return }
            } catch { }
        }
        if ([DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Milliseconds 500
    } while ($true)
    throw "$($Definition.Name) did not answer readiness checks; no replacement was started. Check its log and broker connectivity."
}

function Start-NativeBackgroundServices {
    param(
        [switch]$SkipRagWorker, [switch]$SkipMigrations,
        [ValidateSet('tgo-rag-worker', 'tgo-workflow-worker', 'tgo-plugin-runtime')]
        [string[]]$ServiceNames = @('tgo-rag-worker', 'tgo-workflow-worker', 'tgo-plugin-runtime')
    )

    $definitions = @(Get-NativeBackgroundServices | Where-Object {
        $_.Name -in $ServiceNames -and -not ($SkipRagWorker -and $_.Name -eq 'tgo-rag-worker')
    })
    $state = @()
    if (Test-Path -LiteralPath $script:StateFile) {
        $state = @(Read-ProcessState)
    }
    foreach ($definition in $definitions) {
        $python = Join-Path $script:RepoRoot "$($definition.Directory)\.venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $python)) {
            throw "Missing native dependency: $python. Run scripts\native-dev\install.ps1 first."
        }
    }

    foreach ($definition in $definitions) {
        $directory = Join-Path $script:RepoRoot $definition.Directory
        $python = Join-Path $directory '.venv\Scripts\python.exe'
        Set-NativeEnvironment -Service $definition.Environment
        foreach ($otherNode in $definition.ConflictingWorkerNodes) {
            if (Test-NativeWorkerReady -Python $python -Node $otherNode) {
                throw "$($definition.Name): existing worker $otherNode answered on the same broker. No process was started or stopped. Resolve Docker/native ownership before recovery."
            }
        }
        $existing = @($state | Where-Object { $_.name -eq $definition.Name })
        $running = @($existing | Where-Object { Test-NativeProcessIdentity -Entry $_ })
        if ($running.Count -gt 0) {
            Wait-NativeBackgroundReady -Definition $definition -Entry $running[0] -Python $python
            Write-Host "$($definition.Name) is already running and ready."
            continue
        }
        foreach ($port in $definition.Ports) {
            Assert-PortAvailable -Port $port -Label $definition.Name
        }
        if ($definition.Environment -eq 'plugin' -and -not $SkipMigrations) {
            Push-Location $directory
            try {
                & $python -m alembic upgrade head
                if ($LASTEXITCODE -ne 0) { throw 'Plugin runtime migration failed.' }
            } finally { Pop-Location }
        }
        Write-Host "Starting $($definition.Name)..."
        $process = Start-NativeProcess -Name $definition.Name -FilePath $python `
            -ArgumentList $definition.Arguments -WorkingDirectory $directory
        $state = @($state | Where-Object { $_.name -ne $definition.Name }) + @($process)
        Save-ProcessState -Processes $state
        Wait-NativeBackgroundReady -Definition $definition -Entry $process -Python $python
    }
}
