# Threads/Load-EmbeddedFunctions.ps1
# This file is a thin shim that delegates to FUNCTIONS/Discovery/Load-EmbeddedFunctions.ps1.
# 
# The Discovery version has the full implementation including SkipImport support,
# object-based return types, and proper error handling.
#
# NOTE: This shim does NOT define Load-EmbeddedFunctions itself — it simply
# re-exports the Discovery version to avoid duplicate function definitions
# when Import-Functions.ps1 dot-sources both files.

$discoveryPath = Join-Path $PSScriptRoot "..\Discovery\Load-EmbeddedFunctions.ps1"
if (Test-Path $discoveryPath) {
    . $discoveryPath
} else {
    Write-Warning "Discovery/Load-EmbeddedFunctions.ps1 not found — embedded function loading unavailable."
    
    # Provide minimal stub implementations to avoid breaking callers
    function Load-EmbeddedFunctions {
        [CmdletBinding()]
        param(
            [Parameter(Mandatory=$true)]
            [string]$DatagramPath,
            [switch]$ForceReload,
            [switch]$SkipImport
        )
        Write-Warning "Load-EmbeddedFunctions: Discovery version not available."
        return @()
    }
    
    function Invoke-EmbeddedFunction {
        [CmdletBinding()]
        param(
            [Parameter(Mandatory=$true)]
            [string]$FunctionName,
            [Parameter(Mandatory=$true)]
            [hashtable]$LoadedFunctions,
            [object[]]$Arguments
        )
        Write-Error "Invoke-EmbeddedFunction: Discovery version not available."
    }
    
    Export-ModuleMember -Function Load-EmbeddedFunctions, Invoke-EmbeddedFunction
}
