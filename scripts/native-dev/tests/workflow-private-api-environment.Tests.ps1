$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../common.ps1')

$script:qaWorkflowEnvironment = @{ API_INTERNAL_SERVICE_URL = 'http://legacy-container:8001' }
function Import-DotEnv { param([string]$Path) }
function Get-EnvValue {
    param([string]$Name, [string]$DefaultValue)
    if ($script:qaWorkflowEnvironment.ContainsKey($Name)) { return $script:qaWorkflowEnvironment[$Name] }
    return $DefaultValue
}
function Set-ProcessEnv {
    param([string]$Name, [string]$Value)
    $script:qaWorkflowEnvironment[$Name] = $Value
}

Set-NativeEnvironment -Service workflow
if ($script:qaWorkflowEnvironment['API_INTERNAL_SERVICE_URL'] -ne 'http://127.0.0.1:18001') {
    throw 'Workflow API and Worker must verify source authority through the native private API'
}
if ($script:qaWorkflowEnvironment['AI_SERVICE_URL'] -ne 'http://127.0.0.1:8081') {
    throw 'Workflow must retain the native AI service address'
}
Write-Output 'Workflow native private API environment: 2 checks passed'
