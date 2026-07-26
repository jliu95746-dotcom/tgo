param(
    [string]$ShortcutName = 'TGO 客服系统.lnk',
    [string]$SimulatorShortcutName = 'TGO 客户模拟器.lnk',
    [string]$WeComPlatformId
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$launcher = Join-Path $PSScriptRoot 'launch.ps1'
$desktop = [Environment]::GetFolderPath('Desktop')
$powershell = Join-Path $PSHOME 'powershell.exe'

if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Launcher not found: $launcher"
}

function New-TgoShortcut {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $shortcutPath = Join-Path $desktop $Name
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.IconLocation = "$env:SystemRoot\System32\imageres.dll,11"
    $shortcut.Description = $Description
    $shortcut.Save()

    Write-Host "Desktop shortcut created: $shortcutPath"
}

$baseArguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
New-TgoShortcut `
    -Name $ShortcutName `
    -Arguments $baseArguments `
    -Description '启动 TGO 混合服务并打开管理员页面'

if (-not [string]::IsNullOrWhiteSpace($WeComPlatformId)) {
    $parsedPlatformId = [Guid]::Empty
    if (-not [Guid]::TryParse($WeComPlatformId, [ref]$parsedPlatformId)) {
        throw '企业微信渠道 ID 格式不正确，未创建客户模拟器快捷方式。'
    }

    $simulatorArguments = "$baseArguments -OpenCustomerSimulator -WeComPlatformId `"$($parsedPlatformId.ToString())`""
    New-TgoShortcut `
        -Name $SimulatorShortcutName `
        -Arguments $simulatorArguments `
        -Description '启动 TGO 混合服务并打开企业微信客户模拟器'
}
