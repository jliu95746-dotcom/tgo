# Creates a database archive only; no migration, restart or feature activation.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$backupId = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N')
$backupRoot = [IO.Path]::GetFullPath((Join-Path $script:RepoRoot '.tmp\backups'))
$backupDirectory = [IO.Path]::GetFullPath((Join-Path $backupRoot $backupId))
if (-not $backupDirectory.StartsWith($backupRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup directory is outside the intended workspace folder.'
}
New-Item -ItemType Directory -Path $backupDirectory -ErrorAction Stop | Out-Null
$localDump = Join-Path $backupDirectory 'database.dump'
$remoteDump = '/tmp/yujian-before-operators-' + $backupId + '.dump'

$composeArguments = Get-ComposeArguments -Tail @('config', '--quiet')
& docker @composeArguments
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the configured Docker project; no backup attempted.' }

# Remote shell expands container-owned credentials; none are printed or passed
# as host command arguments. Positional arguments keep the file path separate.
$dumpCommand = 'umask 077; exec pg_dump --no-password -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --file="$1"'
$composeArguments = Get-ComposeArguments -Tail @('exec', '-T', 'postgres', 'sh', '-c', $dumpCommand, 'backup', $remoteDump)
& docker @composeArguments
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed; no database changes were made.' }

$composeArguments = Get-ComposeArguments -Tail @('exec', '-T', 'postgres', 'pg_restore', '--list', $remoteDump)
$archiveContents = @(& docker @composeArguments)
if ($LASTEXITCODE -ne 0 -or $archiveContents.Count -eq 0) { throw 'Archive listing failed; do not apply migrations.' }

$composeArguments = Get-ComposeArguments -Tail @('cp', ('postgres:' + $remoteDump), $localDump)
& docker @composeArguments
if ($LASTEXITCODE -ne 0) { throw 'Could not copy the archive to the local backup directory.' }

$stream = [IO.File]::OpenRead($localDump)
try {
    $signature = New-Object byte[] 5
    if ($stream.Read($signature, 0, 5) -ne 5 -or [Text.Encoding]::ASCII.GetString($signature) -ne 'PGDMP') {
        throw 'Local file is not a PostgreSQL custom archive.'
    }
} finally {
    $stream.Dispose()
}
$manifest = [ordered]@{
    createdAtUtc = [DateTime]::UtcNow.ToString('o')
    backupPath = $localDump
    bytes = (Get-Item -LiteralPath $localDump).Length
    sha256 = (Get-FileHash -LiteralPath $localDump -Algorithm SHA256).Hash
    archiveListingVerified = $true
    archiveListingLines = $archiveContents.Count
    migrationApplied = $false
}
$manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $backupDirectory 'manifest.json') -Encoding UTF8
Write-Output 'PASS: database backup created and archive list verified; no migration applied.'
Write-Output ('Backup: ' + $localDump)
