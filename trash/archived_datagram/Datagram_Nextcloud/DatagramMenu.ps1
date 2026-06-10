<#
.SYNOPSIS
Datagram Loader Menu - PowerShell command-line interface for loading and examining datagrams.
.DESCRIPTION
Provides interactive menu to load datagrams, test hash, view GUI, connect to database,
load embedded functions, and test compatibility. This is the "cmd version" referenced
in the work order.
.PARAMETER DatagramPath
Optional path to a datagram folder. If provided, skips menu and loads directly.
.EXAMPLE
.\DatagramMenu.ps1
Interactive menu.
.EXAMPLE
.\DatagramMenu.ps1 -DatagramPath "C:\path\to\datagram"
Loads the specified datagram directly.
.NOTES
Requires BouncyCastle.Cryptography.dll for SHAKE256 hash (run Download-BouncyCastle.ps1).
#>
param(
    [string]$DatagramPath
)

# Import Datagram functions
$FunctionsRoot = Join-Path $PSScriptRoot "FUNCTIONS"
if (Test-Path $FunctionsRoot) {
    . (Join-Path $FunctionsRoot "Import-Functions.ps1")
} else {
    Write-Host "ERROR: FUNCTIONS folder not found. Ensure script is run from Datagram project root." -ForegroundColor Red
    exit 1
}

# Banner
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "    Datagram Loader Menu (PowerShell)     " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Check for BouncyCastle DLL
$bouncyCastleDll = Join-Path $PSScriptRoot "lib\BouncyCastle.Cryptography.dll"
if (-not (Test-Path $bouncyCastleDll)) {
    Write-Host "WARNING: BouncyCastle.Cryptography.dll not found." -ForegroundColor Yellow
    Write-Host "  SHAKE256 hash algorithm will not work." -ForegroundColor Yellow
    Write-Host "  To download, run: .\lib\Download-BouncyCastle.ps1" -ForegroundColor Yellow
    Write-Host ""
}

# If datagram path provided, load directly
if ($DatagramPath -and (Test-Path $DatagramPath)) {
    Write-Host "Loading datagram: $DatagramPath" -ForegroundColor Green
    $datagram = Load-Datagram -Path $DatagramPath
    if ($datagram) {
        Show-DatagramGUI -Datagram $datagram
    }
    exit
}

# Interactive menu
do {
    Write-Host "=== MAIN MENU ===" -ForegroundColor Green
    Write-Host "1. Load Datagram"
    Write-Host "2. Test Datagram Hash"
    Write-Host "3. Show Datagram GUI"
    Write-Host "4. Connect to Database"
    Write-Host "5. Load Embedded Functions"
    Write-Host "6. Test Compatibility"
    Write-Host "7. Run BouncyCastle Download Script"
    Write-Host "8. Exit"
    Write-Host ""
    
    $choice = Read-Host "Enter choice (1-8)"
    
    switch ($choice) {
        '1' {
            $path = Read-Host "Enter datagram folder path"
            if (Test-Path $path) {
                $datagram = Load-Datagram -Path $path
                if ($datagram) {
                    Write-Host "Datagram loaded successfully." -ForegroundColor Green
                    $global:CurrentDatagram = $datagram
                } else {
                    Write-Host "Failed to load datagram." -ForegroundColor Red
                }
            } else {
                Write-Host "Path not found." -ForegroundColor Red
            }
        }
        '2' {
            if (-not $global:CurrentDatagram) {
                Write-Host "No datagram loaded. Please load a datagram first." -ForegroundColor Yellow
                break
            }
            $expectedHash = $global:CurrentDatagram.Hash
            $valid = Test-DatagramHash -Path $global:CurrentDatagram.Path -ExpectedHash $expectedHash
            if ($valid) {
                Write-Host "Hash validation PASSED." -ForegroundColor Green
            } else {
                Write-Host "Hash validation FAILED." -ForegroundColor Red
            }
        }
        '3' {
            if (-not $global:CurrentDatagram) {
                Write-Host "No datagram loaded. Please load a datagram first." -ForegroundColor Yellow
                break
            }
            Show-DatagramGUI -Datagram $global:CurrentDatagram
        }
        '4' {
            if (-not $global:CurrentDatagram) {
                Write-Host "No datagram loaded. Please load a datagram first." -ForegroundColor Yellow
                break
            }
            $conn = Connect-DatagramDatabase -DatagramPath $global:CurrentDatagram.Path
            if ($conn) {
                Write-Host "Database connected." -ForegroundColor Green
                $global:CurrentDatabase = $conn
            }
        }
        '5' {
            if (-not $global:CurrentDatagram) {
                Write-Host "No datagram loaded. Please load a datagram first." -ForegroundColor Yellow
                break
            }
            $functions = Load-EmbeddedFunctions -DatagramPath $global:CurrentDatagram.Path
            Write-Host "Loaded $($functions.Count) embedded functions." -ForegroundColor Green
            $global:LoadedFunctions = $functions
        }
        '6' {
            if (-not $global:CurrentDatagram) {
                Write-Host "No datagram loaded. Please load a datagram first." -ForegroundColor Yellow
                break
            }
            $compat = Test-DatagramCompatibility -Datagram $global:CurrentDatagram
            if ($compat) {
                Write-Host "Compatibility test PASSED." -ForegroundColor Green
            } else {
                Write-Host "Compatibility test FAILED." -ForegroundColor Red
            }
        }
        '7' {
            $downloadScript = Join-Path $PSScriptRoot "lib\Download-BouncyCastle.ps1"
            if (Test-Path $downloadScript) {
                Write-Host "Running BouncyCastle download script..." -ForegroundColor Cyan
                & $downloadScript
            } else {
                Write-Host "Download script not found at $downloadScript" -ForegroundColor Red
            }
        }
        '8' {
            Write-Host "Exiting. Goodbye!" -ForegroundColor Cyan
            exit
        }
        default {
            Write-Host "Invalid choice. Please enter a number between 1 and 8." -ForegroundColor Red
        }
    }
    
    Write-Host ""
    pause
} while ($true)
