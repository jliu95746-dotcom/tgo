. (Join-Path $PSScriptRoot '../common.ps1')

# Synthetic process configuration: never load or display local credentials.
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
Set-NativeEnvironment -Service rag
if ($script:qaEnvironment['SAAS_API_INTERNAL_URL'] -ne 'http://127.0.0.1:18001') { throw 'RAG quota authorization must target the native private API port' }
if ($script:qaEnvironment.ContainsKey('SAAS_BILLING_ENABLED') -or $script:qaEnvironment.ContainsKey('SAAS_NEW_PURCHASES_ENABLED')) { throw 'Native address setup must not enable billing or purchases' }
Write-Output 'PASS: native RAG quota URL uses the internal port; billing switches unchanged'
