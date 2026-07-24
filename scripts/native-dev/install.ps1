param(
    [switch]$Force
)

. (Join-Path $PSScriptRoot 'common.ps1')

Ensure-RuntimeDirectory

$systemPython = (Get-Command python -ErrorAction Stop).Source
$toolsVenv = Join-Path $script:RuntimeDir 'poetry-venv'
$toolsPython = Join-Path $toolsVenv 'Scripts\python.exe'

if ($Force -or -not (Test-Path -LiteralPath $toolsPython)) {
    Write-Host 'Creating local Poetry tool environment...'
    & $systemPython -m venv $toolsVenv
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to create Poetry tool environment.'
    }
}

$poetryAvailable = & $toolsPython -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('poetry') else 1)"
if ($Force -or $LASTEXITCODE -ne 0) {
    Write-Host 'Installing Poetry into the project-local tool environment...'
    & $toolsPython -m pip install --disable-pip-version-check --upgrade pip poetry
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to install Poetry.'
    }
}

$env:POETRY_VIRTUALENVS_IN_PROJECT = 'true'

foreach ($service in @('tgo-api', 'tgo-ai', 'tgo-rag', 'tgo-workflow')) {
    $serviceDirectory = Join-Path $script:RepoRoot "repos\$service"
    Write-Host "Installing locked Python dependencies for $service..."
    Push-Location $serviceDirectory
    try {
        & $toolsPython -m poetry install --with dev --no-interaction
        if ($LASTEXITCODE -ne 0) {
            throw "Poetry install failed for $service."
        }
    } finally {
        Pop-Location
    }
}

$corepack = (Get-Command corepack.cmd -ErrorAction Stop).Source
foreach ($frontend in @('tgo-web', 'tgo-widget-js')) {
    $frontendDirectory = Join-Path $script:RepoRoot "repos\$frontend"
    Write-Host "Installing locked frontend dependencies for $frontend..."
    Push-Location $frontendDirectory
    try {
        & $corepack yarn install --frozen-lockfile --non-interactive --production=false --network-timeout 600000
        if ($LASTEXITCODE -ne 0) {
            throw "Yarn install failed for $frontend."
        }
    } finally {
        Pop-Location
    }
}

Write-Host 'Native application dependencies are ready.'
