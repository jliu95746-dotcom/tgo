$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '..\common.ps1')
. (Join-Path $PSScriptRoot '..\background-services.ps1')

function Assert-True($Value, [string]$Message) {
    if (-not $Value) { throw $Message }
}
$definitions = @(Get-NativeBackgroundServices)
$worker = $definitions | Where-Object Name -eq 'tgo-workflow-worker'
$plugin = $definitions | Where-Object Name -eq 'tgo-plugin-runtime'
$entry = [pscustomobject]@{ name = $worker.Name; pid = 999 }
$script:alive = $true
$script:pong = $false
$script:probeCount = 0
function Test-NativeProcessIdentity { param($Entry) return $script:alive }
function Test-NativeWorkerReady { param($Python, $Node) $script:probeCount++; return $script:pong }
function Invoke-WebRequest { param($Uri, [switch]$UseBasicParsing, $TimeoutSec) return [pscustomobject]@{ StatusCode = 200 } }

$failed = $false
try { Wait-NativeBackgroundReady $worker $entry 'fixture' -TimeoutSeconds 0 } catch { $failed = $true }
Assert-True $failed 'A live process without worker pong must not count as ready'
Assert-True ($script:probeCount -eq 1) 'Timeout must be bounded'
$script:pong = $true
Wait-NativeBackgroundReady $worker $entry 'fixture' -TimeoutSeconds 0
$script:alive = $false
$failed = $false
try { Wait-NativeBackgroundReady $worker $entry 'fixture' -TimeoutSeconds 0 } catch { $failed = $true }
Assert-True $failed 'Exited process must fail even if a stale pong exists'
$script:alive = $true
Wait-NativeBackgroundReady $plugin $entry 'fixture' -TimeoutSeconds 0

# The orchestration tests use only in-memory state and never start/stop a process.
$script:testState = @()
$script:started = @()
$script:verified = @()
$script:environments = @()
function Read-ProcessState { return $script:testState }
function Save-ProcessState { param($Processes) $script:testState = @($Processes) }
function Set-NativeEnvironment { param($Service) $script:environments += $Service }
function Assert-PortAvailable { param($Port, $Label) }
function Start-NativeProcess {
    param($Name, $FilePath, $ArgumentList, $WorkingDirectory)
    $script:started += $Name
    return [pscustomobject]@{ name = $Name; pid = 999 }
}
function Wait-NativeBackgroundReady {
    param($Definition, $Entry, $Python)
    $script:verified += $Definition.Name
}
Start-NativeBackgroundServices -ServiceNames 'tgo-workflow-worker' -SkipMigrations
Assert-True ($script:started.Count -eq 1 -and $script:started[0] -eq 'tgo-workflow-worker') 'Targeted recovery must not start unrelated services'
Assert-True ($script:environments[0] -eq 'workflow') 'Probe must use the selected worker broker environment'
Start-NativeBackgroundServices -ServiceNames 'tgo-workflow-worker' -SkipMigrations
Assert-True ($script:started.Count -eq 1) 'A running worker must not be duplicated'
Assert-True ($script:verified.Count -eq 2) 'Already running workers must still pass readiness'
Start-NativeBackgroundServices -ServiceNames 'tgo-rag-worker' -SkipRagWorker -SkipMigrations
Assert-True ($script:started.Count -eq 1) 'Explicit skip must still be honored'
$failed = $false
try { Start-NativeBackgroundServices -ServiceNames 'tgo-rag-worker' -SkipMigrations } catch { $failed = $_.Exception.Message -match 'existing worker' }
Assert-True $failed 'An existing Docker RAG worker must block a duplicate native consumer'
Assert-True ($script:started.Count -eq 1) 'A detected worker conflict must not mutate processes'
Write-Host 'PASS: no-pong, ready, exited, HTTP, targeted recovery, correct broker, no duplicate, existing readiness, skip, Docker conflict'
