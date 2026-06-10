<#
.SYNOPSIS
Loads embedded functions from a datagram.
.DESCRIPTION
Searches for embedded PowerShell function definitions within a datagram, typically located in PreLoad/Intil folder or specified in Meta/EmbeddedFunctions.ini.
The functions are dot‑sourced and made available in the current session. Returns an array of loaded function names.
.PARAMETER DatagramPath
Path to the datagram root folder.
.PARAMETER ForceReload
If specified, reloads functions even if they were already loaded previously.
.PARAMETER SkipImport
If specified, only scans and returns function definitions without actually importing them.
.EXAMPLE
Load-EmbeddedFunctions -DatagramPath "C:\Datagram\MyDatagram"
Loads all embedded PowerShell functions from the datagram.
.EXAMPLE
Load-EmbeddedFunctions -DatagramPath "C:\Datagram\MyDatagram" -SkipImport
Scans for embedded functions and returns their names without importing.
.OUTPUTS
[array] Array of loaded function names (or function info objects if SkipImport is used).
.NOTES
Part of the Datagram Loader System. Version 1.0.0.
#>
function Load-EmbeddedFunctions {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$DatagramPath,

        [switch]$ForceReload,

        [switch]$SkipImport
    )

    Write-Verbose "Loading embedded functions from datagram: $DatagramPath"

    # Validate datagram path
    if (-not (Test-Path $DatagramPath)) {
        Write-Error "Datagram path not found: $DatagramPath"
        return @()
    }

    # Look for embedded function definitions
    $embeddedFunctions = @()

    # 1. Check for Meta/EmbeddedFunctions.ini (structured manifest)
    $manifestPath = Join-Path $DatagramPath "Meta\EmbeddedFunctions.ini"
    if (Test-Path $manifestPath) {
        Write-Verbose "Found embedded functions manifest: $manifestPath"
        $manifestContent = Get-Content $manifestPath -Raw
        # Simple INI parsing: each line [FunctionName]={Path}
        $lines = $manifestContent -split "`r?`n"
        foreach ($line in $lines) {
            $trimmed = $line.Trim()
            if ($trimmed -match '^\[([^\]]+)\]=\{(.*)\}$') {
                $funcName = $matches[1]
                $funcPath = $matches[2]
                $fullPath = Join-Path $DatagramPath $funcPath
                if (Test-Path $fullPath) {
                    $embeddedFunctions += [PSCustomObject]@{
                        Name = $funcName
                        Path = $fullPath
                        Source = 'manifest'
                    }
                } else {
                    Write-Warning "Embedded function '$funcName' references missing file: $funcPath"
                }
            }
        }
    }

    # 2. Scan PreLoad/Intil folder for .ps1 files (default location)
    $intilPath = Join-Path $DatagramPath "PreLoad\Intil"
    if (Test-Path $intilPath) {
        Write-Verbose "Scanning Intil folder for PowerShell scripts: $intilPath"
        $ps1Files = Get-ChildItem -Path $intilPath -Filter "*.ps1" -File -Recurse
        foreach ($file in $ps1Files) {
            # Try to infer function name from file content (look for 'function <name>' pattern)
            $content = Get-Content $file.FullName -Raw -ErrorAction SilentlyContinue
            $funcName = $null
            if ($content -match 'function\s+([\w\-]+)') {
                $funcName = $matches[1]
            } else {
                $funcName = $file.BaseName
            }
            $embeddedFunctions += [PSCustomObject]@{
                Name = $funcName
                Path = $file.FullName
                Source = 'intil'
            }
        }
    }

    # 3. If no embedded functions found, return empty array
    if ($embeddedFunctions.Count -eq 0) {
        Write-Verbose "No embedded functions found in datagram."
        return @()
    }

    Write-Verbose "Found $($embeddedFunctions.Count) embedded function(s)."

    # Import functions unless SkipImport is specified
    $loaded = @()
    if (-not $SkipImport) {
        foreach ($func in $embeddedFunctions) {
            try {
                Write-Verbose "Importing function '$($func.Name)' from $($func.Path)"
                . $func.Path  # dot‑source the script
                $loaded += $func.Name
                Write-Verbose "Successfully imported '$($func.Name)'."
            } catch {
                Write-Warning "Failed to import '$($func.Name)' from $($func.Path): $_"
            }
        }
        Write-Host "Loaded $($loaded.Count) embedded function(s)." -ForegroundColor Green
        return $loaded
    } else {
        # Return function info objects
        return $embeddedFunctions
    }
}

Export-ModuleMember -Function Load-EmbeddedFunctions