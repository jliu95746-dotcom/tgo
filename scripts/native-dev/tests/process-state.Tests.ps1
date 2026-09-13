. (Join-Path $PSScriptRoot '..\common.ps1')
$script:RuntimeDir = Join-Path $script:RepoRoot ('.tmp\process-state-test-' + [guid]::NewGuid().ToString('N'))
$script:StateFile = Join-Path $script:RuntimeDir 'processes.json'
Ensure-RuntimeDirectory
$unicodePath = 'E:\' + [char]0x667a + [char]0x80fd + '\python.exe'
$entry = [pscustomobject]@{ name = 'test-service'; pid = 12345; executable = $unicodePath; workingDirectory = 'E:\test'; startedAt = '2026-09-09T12:00:00-04:00' }

if (@(Read-ProcessState).Count -ne 0) { throw 'Missing state must be empty' }
Save-ProcessState -Processes @($entry)
if (-not (Get-Content -Raw -Encoding UTF8 $script:StateFile).Trim().StartsWith('[')) { throw 'Single entry must stay an array' }
$loaded = @(Read-ProcessState)
if ($loaded.Count -ne 1 -or $loaded[0].executable -ne $unicodePath) { throw 'Unicode round trip failed' }

# Windows PowerShell may serialize an adapted array as a value/Count wrapper.
$legacy = @([pscustomobject]@{ value = @([pscustomobject]@{ value = @($entry); Count = 1 }); Count = 1 }, $entry)
ConvertTo-Json -InputObject $legacy -Depth 10 | Set-Content -LiteralPath $script:StateFile -Encoding UTF8
$loaded = @(Read-ProcessState)
if ($loaded.Count -ne 2 -or $loaded[0].name -ne $entry.name) { throw 'Legacy wrapper must be flattened without dropping records' }
Save-ProcessState -Processes $loaded
if ((Get-Content -Raw -Encoding UTF8 $script:StateFile) -match '"value"') { throw 'Saved records must not retain array wrappers' }

Save-ProcessState -Processes @()
if (@(Read-ProcessState).Count -ne 0) { throw 'Empty state must round trip' }
'{"unexpected":true}' | Set-Content -LiteralPath $script:StateFile -Encoding UTF8
$rejected = $false
try { $null = @(Read-ProcessState) } catch { $rejected = $true }
if (-not $rejected) { throw 'Unknown records must fail closed' }
Write-Host 'PASS: missing, single, Unicode, nested legacy, normalized save, empty, invalid state'
