$ErrorActionPreference = 'Stop'
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot '..\backup-before-operators.ps1')).Path
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$backupRoot = [IO.Path]::GetFullPath((Join-Path $repoRoot '.tmp\backups'))

function global:docker {
    $dockerArgs = @($args)
    $command = [string]::Join(' ', [string[]]$dockerArgs)
    $global:BackupCleanupMockCalls += $command

    if ($command.Contains('pg_dump')) {
        $global:BackupCleanupMockRemoteDump = [string]$dockerArgs[-1]
        if ($global:BackupCleanupMockMode -eq 'dump-fail') {
            $global:LASTEXITCODE = 17
            return
        }
    }
    if ($dockerArgs -contains '--list') {
        $global:LASTEXITCODE = 0
        return @('; archive header', 'TABLE public.api_staff')
    }
    if ($dockerArgs -contains 'cp') {
        [IO.File]::WriteAllBytes([string]$dockerArgs[-1], [byte[]](80, 71, 68, 77, 80, 1, 2, 3))
    }
    if ($command.Contains('rm -f --') -and $global:BackupCleanupMockMode -eq 'cleanup-fail') {
        $global:LASTEXITCODE = 18
        return
    }
    $global:LASTEXITCODE = 0
}

function Invoke-BackupScenario {
    param([Parameter(Mandatory = $true)][string]$Mode)

    $global:BackupCleanupMockMode = $Mode
    $global:BackupCleanupMockCalls = @()
    $global:BackupCleanupMockRemoteDump = $null
    $result = @()
    $failure = $null
    try {
        $result = @(& $scriptPath)
    } catch {
        $failure = $_
    }

    $remoteDump = $global:BackupCleanupMockRemoteDump
    if ($remoteDump -notmatch '^/tmp/yujian-before-operators-(\d{8}-\d{6}-[0-9a-f]{32})\.dump$') {
        throw 'Backup test did not observe a generated archive path.'
    }
    $backupId = $Matches[1]
    $backupDirectory = [IO.Path]::GetFullPath((Join-Path $backupRoot $backupId))
    if (-not $backupDirectory.StartsWith($backupRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to clean outside the test backup directory.'
    }

    try {
        $cleanupCalls = @($global:BackupCleanupMockCalls | Where-Object { $_.Contains('rm -f --') })
        if ($cleanupCalls.Count -ne 1 -or -not $cleanupCalls[0].Contains($remoteDump)) {
            throw "Expected one cleanup of the generated archive in mode $Mode."
        }
        if ($Mode -eq 'success') {
            if ($failure -or -not (Test-Path -LiteralPath (Join-Path $backupDirectory 'manifest.json'))) {
                throw 'Successful backup must create a manifest and report success.'
            }
            if (-not (@($result | Where-Object { $_ -like 'PASS:*' }).Count -eq 1)) {
                throw 'Successful backup did not report success.'
            }
        } elseif ($Mode -eq 'dump-fail') {
            if (-not $failure -or $failure.Exception.Message -notmatch 'pg_dump failed') {
                throw 'Failed pg_dump must keep its original error.'
            }
            if (Test-Path -LiteralPath (Join-Path $backupDirectory 'manifest.json')) {
                throw 'Failed pg_dump must not create a manifest.'
            }
        } elseif ($Mode -eq 'cleanup-fail') {
            if (-not $failure -or $failure.Exception.Message -notmatch 'cleanup failed') {
                throw 'Failed cleanup must stop the backup from reporting success.'
            }
        }
    } finally {
        if (Test-Path -LiteralPath $backupDirectory) {
            Remove-Item -LiteralPath $backupDirectory -Recurse -Force
        }
    }
}

try {
    Invoke-BackupScenario -Mode 'success'
    Invoke-BackupScenario -Mode 'dump-fail'
    Invoke-BackupScenario -Mode 'cleanup-fail'
    Write-Output 'PASS: backup archive cleanup on success and failure paths'
} finally {
    Remove-Item Function:\docker -ErrorAction SilentlyContinue
    Remove-Variable BackupCleanupMockMode, BackupCleanupMockCalls, BackupCleanupMockRemoteDump -Scope Global -ErrorAction SilentlyContinue
}
