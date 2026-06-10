# Import-Functions.ps1
# Dynamically imports all PowerShell function files from subdirectories

$FunctionsRoot = $PSScriptRoot
Write-Host "Importing functions from $FunctionsRoot..." -ForegroundColor Cyan

# Get all .ps1 files recursively, excluding Import-Functions.ps1 itself
$functionFiles = Get-ChildItem -Path $FunctionsRoot -Filter "*.ps1" -Recurse | Where-Object { $_.Name -ne "Import-Functions.ps1" }

foreach ($file in $functionFiles) {
    try {
        . $file.FullName
        Write-Host "Imported: $($file.FullName)" -ForegroundColor Green
    } catch {
        Write-Host "Failed to import $($file.FullName): $_" -ForegroundColor Red
    }
}

Write-Host "Function import completed. Total functions imported: $($functionFiles.Count)" -ForegroundColor Cyan