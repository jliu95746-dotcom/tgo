param(
    [switch]$SkipMigrations,
    [switch]$SkipRagWorker,
    [switch]$NoBrowser,
    [switch]$OpenCustomerSimulator,
    [string]$WeComPlatformId
)

. (Join-Path $PSScriptRoot 'common.ps1')

Ensure-RuntimeDirectory
Import-DotEnv -Path $script:EnvFile

$webPort = [int](Get-EnvValue -Name 'TGO_WEB_PORT' -DefaultValue '5173')
$widgetPort = [int](Get-EnvValue -Name 'TGO_WIDGET_PORT' -DefaultValue '5174')
$adminUrl = "http://127.0.0.1:$webPort/chat"
$widgetUrl = "http://127.0.0.1:$widgetPort/"
$proxyHealthUrl = "http://127.0.0.1:$webPort/api/v1/setup/status"
$apiDirectory = Join-Path $script:RepoRoot 'repos\tgo-api'
$apiPython = Join-Path $apiDirectory '.venv\Scripts\python.exe'
$launcherLog = Join-Path $script:RuntimeDir 'desktop-launcher.log'
$detailLog = Join-Path $script:RuntimeDir 'desktop-launcher-detail.log'
$lockPath = Join-Path $script:RuntimeDir 'desktop-launcher.lock'
$lockStream = $null
$notification = $null
$transcriptStarted = $false
$cleanupPartialStackOnFailure = $false

function Write-LauncherLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $launcherLog -Value $line -Encoding UTF8
}

function Test-HttpReady {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    } catch {
        return $false
    }
}

function Test-TgoReady {
    foreach ($url in @(
        $adminUrl,
        $proxyHealthUrl,
        'http://127.0.0.1:18000/health',
        'http://127.0.0.1:18001/health',
        'http://127.0.0.1:8081/health',
        'http://127.0.0.1:18082/health',
        'http://127.0.0.1:8003/health',
        'http://127.0.0.1:8004/health',
        'http://127.0.0.1:8085/health',
        'http://127.0.0.1:5174/'
    )) {
        if (-not (Test-HttpReady -Url $url)) {
            return $false
        }
    }
    return $true
}

function Test-DockerEngine {
    $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $dockerCommand) {
        return $false
    }

    try {
        & $dockerCommand.Source version --format '{{.Server.Version}}' *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-DockerDesktop {
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Docker\Docker Desktop.exe'),
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe')
    )) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $null
}

function Show-Notification {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($null -eq $script:notification) {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $script:notification = New-Object System.Windows.Forms.NotifyIcon
        $script:notification.Icon = [System.Drawing.SystemIcons]::Application
        $script:notification.Visible = $true
    }

    $script:notification.BalloonTipTitle = $Title
    $script:notification.BalloonTipText = $Message
    $script:notification.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
    $script:notification.ShowBalloonTip(3000)
}

function Show-LauncherError {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
        "$Message`n`n详细日志：$detailLog",
        'TGO 客服系统启动失败',
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
}

function Get-CustomerSimulatorUrl {
    if ([string]::IsNullOrWhiteSpace($WeComPlatformId)) {
        throw '缺少企业微信渠道 ID，无法打开客户模拟器。'
    }

    $parsedPlatformId = [Guid]::Empty
    if (-not [Guid]::TryParse($WeComPlatformId, [ref]$parsedPlatformId)) {
        throw '企业微信渠道 ID 格式不正确。'
    }
    if (-not (Test-Path -LiteralPath $apiPython)) {
        throw "未找到 tgo-api Python 环境：$apiPython"
    }

    Set-NativeEnvironment -Service api
    $queryScript = @'
import sys
from uuid import UUID

from app.core.database import SessionLocal
from app.models.platform import Platform

db = SessionLocal()
try:
    platform = (
        db.query(Platform)
        .filter(
            Platform.id == UUID(sys.argv[1]),
            Platform.type == 'wecom',
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if platform is None or not platform.api_key:
        raise SystemExit('Active WeCom platform with API key was not found.')
    print(platform.api_key)
finally:
    db.close()
'@

    Push-Location $apiDirectory
    try {
        $apiKey = (& $apiPython -c $queryScript ($parsedPlatformId.ToString()) | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($apiKey)) {
            throw '未找到已启用且配置完整的企业微信渠道。'
        }
    } finally {
        Pop-Location
    }

    $query = 'apiKey={0}&mode=light&lang=zh-CN' -f [Uri]::EscapeDataString($apiKey.Trim())
    return "${widgetUrl}?$query"
}

function Open-RequestedPage {
    if ($NoBrowser) {
        return
    }

    if ($OpenCustomerSimulator) {
        $simulatorUrl = Get-CustomerSimulatorUrl
        Write-LauncherLog -Message 'Opening WeCom customer simulator.'
        Start-Process -FilePath $simulatorUrl
    } else {
        Start-Process -FilePath $adminUrl
    }
}

try {
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    } catch {
        if (Test-TgoReady) {
            Open-RequestedPage
        } else {
            Show-Notification -Title 'TGO 客服系统' -Message '系统正在启动，请稍候。'
        }
        exit 0
    }

    Set-Content -LiteralPath $detailLog -Value '' -Encoding UTF8
    Start-Transcript -Path $detailLog -Append | Out-Null
    $transcriptStarted = $true

    Write-LauncherLog -Message 'Desktop launch requested.'

    if (Test-TgoReady) {
        Write-LauncherLog -Message 'All required endpoints are already healthy.'
        Open-RequestedPage
        exit 0
    }

    Show-Notification -Title 'TGO 客服系统' -Message '正在检查环境并启动服务，请稍候。'

    if (-not (Test-DockerEngine)) {
        $dockerDesktop = Find-DockerDesktop
        if ($null -eq $dockerDesktop) {
            throw '未找到 Docker Desktop，请先安装 Docker Desktop。'
        }

        Write-LauncherLog -Message "Starting Docker Desktop from $dockerDesktop"
        Start-Process -FilePath $dockerDesktop -WindowStyle Hidden | Out-Null

        $dockerDeadline = (Get-Date).AddMinutes(3)
        while ((Get-Date) -lt $dockerDeadline -and -not (Test-DockerEngine)) {
            Start-Sleep -Seconds 2
        }

        if (-not (Test-DockerEngine)) {
            throw 'Docker Desktop 在 3 分钟内没有准备完成。'
        }
    }

    Write-LauncherLog -Message 'Docker engine is ready. Starting the hybrid stack.'
    $cleanupPartialStackOnFailure = $true
    & (Join-Path $PSScriptRoot 'start.ps1') `
        -SkipMigrations:$SkipMigrations `
        -SkipRagWorker:$SkipRagWorker

    if (-not (Test-TgoReady)) {
        throw '启动脚本已结束，但必要服务健康检查没有全部通过。'
    }
    $cleanupPartialStackOnFailure = $false

    Write-LauncherLog -Message "Hybrid stack is healthy at $adminUrl"
    $readyMessage = if ($OpenCustomerSimulator) {
        '系统已启动，正在打开客户模拟器。'
    } else {
        '系统已启动，正在打开管理员页面。'
    }
    Show-Notification -Title 'TGO 客服系统' -Message $readyMessage
    Open-RequestedPage
} catch {
    $message = $_.Exception.Message
    Write-LauncherLog -Message "Launch failed: $message"
    if ($cleanupPartialStackOnFailure) {
        try {
            & (Join-Path $PSScriptRoot 'stop.ps1')
        } catch {
            Write-LauncherLog -Message "Partial-process cleanup failed: $($_.Exception.Message)"
        }
    }
    Show-LauncherError -Message $message
    exit 1
} finally {
    if ($transcriptStarted) {
        try {
            Stop-Transcript | Out-Null
        } catch {
        }
    }
    if ($null -ne $notification) {
        Start-Sleep -Milliseconds 500
        $notification.Dispose()
    }
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
