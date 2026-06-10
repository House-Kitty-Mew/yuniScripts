<#
.SYNOPSIS
Computes SHAKE256 hash using BouncyCastle library.
.DESCRIPTION
Computes SHAKE256 (extendable-output function) hash with configurable output length.
Requires BouncyCastle.Cryptography.dll in ..\..\lib folder.
.PARAMETER InputBytes
Input byte array to hash.
.PARAMETER OutputLength
Output length in bytes (default 128 bytes = 1024 bits).
.PARAMETER FilePath
Path to a file to hash (alternative to InputBytes).
.PARAMETER AsHex
Return hash as hexadecimal string (lowercase).
.EXAMPLE
Get-SHAKE256Hash -InputBytes @(1,2,3) -OutputLength 32
Computes SHAKE256 hash of bytes 0x01,0x02,0x03 with 256-bit output.
.EXAMPLE
Get-SHAKE256Hash -FilePath "data.bin" -AsHex
Computes SHAKE256 1024-bit hash of file and returns hex string.
.NOTES
Depends on BouncyCastle.Cryptography.dll. If missing, run Download-BouncyCastle.ps1.
#>
function Get-SHAKE256Hash {
    [CmdletBinding(DefaultParameterSetName='Bytes')]
    param(
        [Parameter(Mandatory=$true, ParameterSetName='Bytes', Position=0)]
        [byte[]]$InputBytes,
        
        [Parameter(Mandatory=$true, ParameterSetName='File')]
        [string]$FilePath,

        [Parameter(Mandatory=$false)]
        [int]$OutputLength = 128,

        [switch]$AsHex
    )

    # Load BouncyCastle assembly
    $assemblyPath = Join-Path $PSScriptRoot "..\..\lib\BouncyCastle.Cryptography.dll"
    if (-not (Test-Path $assemblyPath)) {
        throw "BouncyCastle.Cryptography.dll not found at $assemblyPath. Please run Download-BouncyCastle.ps1 first."
    }

    Add-Type -Path $assemblyPath -ErrorAction Stop

    # Determine input bytes
    if ($PSCmdlet.ParameterSetName -eq 'File') {
        if (-not (Test-Path $FilePath)) {
            throw "File not found: $FilePath"
        }
        $InputBytes = [System.IO.File]::ReadAllBytes($FilePath)
    }

    # Create SHAKE256 digest
    $digest = New-Object Org.BouncyCastle.Crypto.Digests.ShakeDigest -ArgumentList 256
    $digest.BlockUpdate($InputBytes, 0, $InputBytes.Length)
    $outputBytes = New-Object byte[] $OutputLength
    $digest.DoFinal($outputBytes, 0)

    if ($AsHex) {
        $hex = [BitConverter]::ToString($outputBytes) -replace '-', ''
        return $hex.ToLower()
    } else {
        return $outputBytes
    }
}

Export-ModuleMember -Function Get-SHAKE256Hash