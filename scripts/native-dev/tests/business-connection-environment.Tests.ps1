. (Join-Path $PSScriptRoot '../common.ps1')

# Capture only public settings; never read credentials into the test process.
$script:qaConnectionEnvironment = @{}
function Import-DotEnv { param([string]$Path) }
function Get-EnvValue {
    param([string]$Name, [string]$DefaultValue)
    if ($script:qaConnectionEnvironment.ContainsKey($Name)) { return $script:qaConnectionEnvironment[$Name] }
    return $DefaultValue
}
function Set-ProcessEnv {
    param([string]$Name, [string]$Value)
    $script:qaConnectionEnvironment[$Name] = $Value
}

Set-NativeEnvironment -Service plugin
if ($script:qaConnectionEnvironment['BUSINESS_CONNECTION_API_URL'] -ne 'http://127.0.0.1:18001') {
    throw 'Business adapter must resolve via the native private API'
}
if ($script:qaConnectionEnvironment.ContainsKey('BUSINESS_CONNECTION_CREDENTIAL_KEY')) {
    throw 'Vault key must not be injected into the business adapter'
}
Write-Output 'Native business connection environment: 2 checks passed'
