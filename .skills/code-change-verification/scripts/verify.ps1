[CmdletBinding()]
param(
    [string[]]$Services
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..\..')).Path
$script:verificationFailed = $false

function Invoke-Verification {
    param(
        [string]$Label,
        [string]$Executable,
        [string[]]$Arguments,
        [string]$Directory
    )

    Write-Host "  > $Label"
    Push-Location -LiteralPath $Directory
    try {
        & $Executable @Arguments
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    if ($exitCode -ne 0) {
        Write-Host "  FAIL $Label (exit $exitCode)"
        $script:verificationFailed = $true
    }
    else {
        Write-Host "  PASS $Label"
    }
}

function Report-Unavailable {
    param([string]$Label)
    Write-Host "  UNAVAILABLE $Label"
    $script:verificationFailed = $true
}

if (-not $Services -or $Services.Count -eq 0) {
    $changed = @(& git -C $repoRoot diff --name-only HEAD -- repos)
    if ($LASTEXITCODE -ne 0) { throw 'git diff failed' }
    $untracked = @(& git -C $repoRoot ls-files --others --exclude-standard -- repos)
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed' }
    $Services = @(
        @($changed) + @($untracked) |
            Where-Object { $_ -match '^repos/([^/]+)/' } |
            ForEach-Object { $Matches[1] } |
            Sort-Object -Unique
    )
}

if (-not $Services -or $Services.Count -eq 0) {
    Write-Host 'No changed services detected'
    exit 0
}

Write-Host "Services: $($Services -join ', ')"
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
$go = Get-Command go.exe -ErrorAction SilentlyContinue

foreach ($service in $Services) {
    if ($service -notmatch '^[a-z0-9-]+$') {
        throw "Invalid service name: $service"
    }
    $serviceDir = Join-Path $repoRoot "repos\$service"
    if (-not (Test-Path -LiteralPath $serviceDir -PathType Container)) {
        throw "Service directory not found: $service"
    }
    Write-Host "`n[$service]"

    if ($service -eq 'tgo-device-agent') {
        if ($go) {
            Invoke-Verification 'go vet ./...' $go.Source @('vet', './...') $serviceDir
        }
        else {
            Report-Unavailable 'go.exe for go vet'
        }
        continue
    }

    $pythonPath = Join-Path $serviceDir '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $pythonPath -PathType Leaf) {
        Invoke-Verification 'compileall app' $pythonPath @('-m', 'compileall', '-q', 'app') $serviceDir
        $mypyPath = Join-Path $serviceDir '.venv\Scripts\mypy.exe'
        $flake8Path = Join-Path $serviceDir '.venv\Scripts\flake8.exe'
        $ruffPath = Join-Path $serviceDir '.venv\Scripts\ruff.exe'

        if ($service -ne 'tgo-platform') {
            if (Test-Path -LiteralPath $mypyPath -PathType Leaf) {
                Invoke-Verification 'mypy app' $mypyPath @('app') $serviceDir
            }
            else {
                Report-Unavailable 'mypy for app'
            }
        }
        if (Test-Path -LiteralPath $flake8Path -PathType Leaf) {
            Invoke-Verification 'flake8 app' $flake8Path @('app') $serviceDir
        }
        elseif (Test-Path -LiteralPath $ruffPath -PathType Leaf) {
            Invoke-Verification 'ruff check .' $ruffPath @('check', '.') $serviceDir
            Invoke-Verification 'ruff format --check .' $ruffPath @('format', '--check', '.') $serviceDir
        }
        else {
            Report-Unavailable 'flake8 or ruff for app'
        }
        continue
    }

    $packagePath = Join-Path $serviceDir 'package.json'
    if (Test-Path -LiteralPath $packagePath -PathType Leaf) {
        if (-not $npm) {
            Report-Unavailable 'npm.cmd for Node checks'
            continue
        }
        $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
        if ($service -eq 'tgo-web') {
            foreach ($check in @('type-check', 'lint', 'build')) {
                if ($package.scripts.PSObject.Properties.Name -contains $check) {
                    Invoke-Verification "npm run $check" $npm.Source @('run', $check) $serviceDir
                }
                else {
                    Report-Unavailable "package script $check"
                }
            }
        }
        elseif ($package.scripts.PSObject.Properties.Name -contains 'build') {
            Invoke-Verification 'npm run build' $npm.Source @('run', 'build') $serviceDir
        }
        else {
            Report-Unavailable 'package script build'
        }
        continue
    }

    Report-Unavailable 'configured static verifier for this service'
}

if ($script:verificationFailed) {
    Write-Host "`nSome service checks failed or were unavailable"
    exit 1
}
Write-Host "`nAll selected service checks passed"
