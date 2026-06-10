<#
.SYNOPSIS
Downloads BouncyCastle.Cryptography.dll from NuGet and places it in the lib folder.
.DESCRIPTION
This script downloads the BouncyCastle.Cryptography NuGet package (version 2.6.2),
extracts the DLL for .NET Framework 4.6.1, and copies it to the current directory.
The DLL is required for SHAKE256 hash algorithm in Datagram loader.
.PARAMETER Version
NuGet package version (default 2.6.2).
.PARAMETER Framework
Target .NET framework (default net461).
.EXAMPLE
.\Download-BouncyCastle.ps1
Downloads the DLL and places it in the lib folder.
.NOTES
Requires PowerShell 5.1 or later (for Expand-Archive).
#>
param(
    [string]$Version = "2.6.2",
    [string]$Framework = "net461"
)

$ErrorActionPreference = "Stop"
$packageName = "BouncyCastle.Cryptography"
$nugetUrl = "https://www.nuget.org/api/v2/package/$packageName/$Version"
$tempDir = Join-Path $env:TEMP "BouncyCastle-$Version"
$nupkgPath = Join-Path $tempDir "$packageName.$Version.nupkg"
$extractDir = Join-Path $tempDir "extracted"
$dllName = "$packageName.dll"
$targetDllPath = Join-Path $PSScriptRoot $dllName

Write-Host "Downloading BouncyCastle.Cryptography version $Version..." -ForegroundColor Cyan

# Create temp directory
if (Test-Path $tempDir) {
    Remove-Item $tempDir -Recurse -Force
}
New-Item -ItemType Directory -Path $tempDir | Out-Null

# Download NuGet package
try {
    Invoke-WebRequest -Uri $nugetUrl -OutFile $nupkgPath -UseBasicParsing
}
catch {
    Write-Error "Failed to download NuGet package from $nugetUrl"
    Write-Error $_
    exit 1
}

Write-Host "Extracting package..." -ForegroundColor Cyan

# Extract .nupkg (zip)
# Rename .nupkg to .zip for Expand-Archive compatibility
$zipPath = [System.IO.Path]::ChangeExtension($nupkgPath, ".zip")
Rename-Item -Path $nupkgPath -NewName $zipPath -Force
Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

# Find DLL in lib subfolder
$dllPath = [System.IO.Path]::Combine($extractDir, "lib", $Framework, $dllName)
if (-not (Test-Path $dllPath)) {
    # Try alternative framework name
    $frameworks = @($Framework, "netstandard2.0", "netcoreapp3.1", "net6.0")
    foreach ($fw in $frameworks) {
        $altPath = [System.IO.Path]::Combine($extractDir, "lib", $fw, $dllName)
        if (Test-Path $altPath) {
            $dllPath = $altPath
            break
        }
    }
}

if (-not (Test-Path $dllPath)) {
    Write-Error "Could not find $dllName in extracted package. Available lib folders:"
    Get-ChildItem -Path (Join-Path $extractDir "lib") -Directory | Select-Object -ExpandProperty Name | Write-Host
    exit 1
}

Write-Host "Copying DLL to lib folder..." -ForegroundColor Cyan
Copy-Item -Path $dllPath -Destination $targetDllPath -Force

Write-Host "Cleaning up temporary files..." -ForegroundColor Cyan
Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue

if (Test-Path $targetDllPath) {
    Write-Host "Successfully installed $dllName to $targetDllPath" -ForegroundColor Green
    Write-Host "File size: $((Get-Item $targetDllPath).Length) bytes" -ForegroundColor Green
}
else {
    Write-Error "Failed to copy DLL."
    exit 1
}