. (Join-Path $PSScriptRoot '../common.ps1')

# Capture settings without reading local credentials or changing process environment.
$script:qaEnvironment = @{}
function Import-DotEnv { param([string]$Path) }
function Get-EnvValue {
    param([string]$Name, [string]$DefaultValue)
    if ($script:qaEnvironment.ContainsKey($Name)) { return $script:qaEnvironment[$Name] }
    return $DefaultValue
}
function Set-ProcessEnv {
    param([string]$Name, [string]$Value)
    $script:qaEnvironment[$Name] = $Value
}

Set-NativeEnvironment -Service web
if ($script:qaEnvironment['TGO_DEV_TYPECHECK'] -ne '0') { throw 'Native checker must default to off' }
$script:qaEnvironment['TGO_DEV_TYPECHECK'] = '1'
Set-NativeEnvironment -Service web
if ($script:qaEnvironment['TGO_DEV_TYPECHECK'] -ne '1') { throw 'Explicit checker opt-in must be preserved' }
Write-Output 'Native web environment: 2 checks passed'
