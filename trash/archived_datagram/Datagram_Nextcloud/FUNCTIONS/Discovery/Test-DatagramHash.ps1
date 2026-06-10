# Test-DatagramHash.ps1
# Validates the integrity hash of a datagram

function Test-DatagramHash {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path,
        
        [Parameter(Mandatory=$true)]
        [string]$ExpectedHash,
        
        [switch]$SkipHashValidation
    )

    if ($SkipHashValidation) {
        Write-Warning "Hash validation skipped by user request."
        return $true
    }

    Write-Host "Computing datagram hash..." -ForegroundColor Cyan

    $computedHash = Get-DatagramContentHash -Path $Path
    $hashValid = ($computedHash -eq $ExpectedHash)

    if ($hashValid) {
        Write-Host "Hash validation PASSED." -ForegroundColor Green
    } else {
        Write-Host "Hash validation FAILED." -ForegroundColor Red
        Write-Host "Expected: $ExpectedHash" -ForegroundColor Yellow
        Write-Host "Computed: $computedHash" -ForegroundColor Yellow
    }

    return $hashValid
}

function Get-DatagramContentHash {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path
    )

    # Determine which files to include in hash.
    # According to spec, hash covers entire datagram contents EXCEPT Meta\Base.ini itself.
    $baseIniPath = Join-Path $Path "Meta\Base.ini"
    $allFiles = Get-ChildItem -Path $Path -File -Recurse | Where-Object { $_.FullName -ne $baseIniPath }

    # Sort files by relative path for consistent ordering
    $sortedFiles = $allFiles | Sort-Object FullName

    # Use SHAKE256 1024-bit hash (128 bytes) via BouncyCastle
    $stream = [System.IO.MemoryStream]::new()

    foreach ($file in $sortedFiles) {
        $relativePath = $file.FullName.Substring($Path.Length + 1)
        Write-Verbose "Hashing file: $relativePath"
        
        # Read file bytes
        $bytes = [System.IO.File]::ReadAllBytes($file.FullName)
        $stream.Write($bytes, 0, $bytes.Length)
        
        # Include file path in hash? Spec unclear. We'll include path as UTF-8 bytes.
        $pathBytes = [System.Text.Encoding]::UTF8.GetBytes($relativePath)
        $stream.Write($pathBytes, 0, $pathBytes.Length)
    }

    $stream.Position = 0
    $hashBytes = Get-SHAKE256Hash -InputBytes $stream.ToArray() -OutputLength 128
    $stream.Dispose()

    # Convert to hex string (lowercase)
    $hexHash = [BitConverter]::ToString($hashBytes) -replace '-', ''
    return $hexHash.ToLower()
}

Export-ModuleMember -Function Test-DatagramHash, Get-DatagramContentHash