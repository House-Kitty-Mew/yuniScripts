# Load-EmbeddedFunctions.ps1
# Loads embedded functions from PreLoad/Intil folder

function Load-EmbeddedFunctions {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$DatagramPath,
        
        [switch]$Force
    )

    $intilPath = Join-Path $DatagramPath "PreLoad\Intil"
    if (-not (Test-Path $intilPath)) {
        Write-Verbose "No Intil folder found; no embedded functions to load."
        return @()
    }

    Write-Host "Loading embedded functions from: $intilPath" -ForegroundColor Cyan

    $scriptFiles = Get-ChildItem -Path $intilPath -File -Recurse -Include *.ps1, *.py, *.js, *.lua, *.bat, *.cmd
    $loadedFunctions = @()

    foreach ($file in $scriptFiles) {
        $ext = $file.Extension.ToLower()
        $relativePath = $file.FullName.Substring($DatagramPath.Length + 1)
        
        Write-Host "Found embedded script: $relativePath" -ForegroundColor Yellow

        switch ($ext) {
            '.ps1' {
                # PowerShell script: dot-source it
                try {
                    . $file.FullName
                    Write-Host "  PowerShell script loaded." -ForegroundColor Green
                    $loadedFunctions += @{
                        Name = $file.BaseName
                        Path = $file.FullName
                        Type = 'PowerShell'
                    }
                } catch {
                    Write-Warning "Failed to load PowerShell script $($file.Name): $_"
                }
            }
            '.py' {
                # Python script: can be invoked via python.exe
                Write-Host "  Python script (requires Python interpreter)." -ForegroundColor Yellow
                $loadedFunctions += @{
                    Name = $file.BaseName
                    Path = $file.FullName
                    Type = 'Python'
                }
            }
            '.js' {
                # JavaScript: can be invoked via node
                Write-Host "  JavaScript (requires Node.js)." -ForegroundColor Yellow
                $loadedFunctions += @{
                    Name = $file.BaseName
                    Path = $file.FullName
                    Type = 'JavaScript'
                }
            }
            default {
                # Other script types: note but don't load
                Write-Host "  Script type $ext not auto-loadable." -ForegroundColor Gray
                $loadedFunctions += @{
                    Name = $file.BaseName
                    Path = $file.FullName
                    Type = $ext.TrimStart('.')
                }
            }
        }
    }

    Write-Host "Loaded $($loadedFunctions.Count) embedded functions." -ForegroundColor Green
    return $loadedFunctions
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

    $func = $LoadedFunctions | Where-Object { $_.Name -eq $FunctionName }
    if (-not $func) {
        Write-Error "Embedded function '$FunctionName' not found."
        return
    }

    Write-Host "Invoking embedded function: $FunctionName ($($func.Type))" -ForegroundColor Cyan

    switch ($func.Type) {
        'PowerShell' {
            # Assume function is defined after dot-sourcing
            # We could re-dot-source or call via & $func.Path
            & $func.Path @Arguments
        }
        'Python' {
            $pythonArgs = @($func.Path) + $Arguments
            Start-Process -FilePath "python" -ArgumentList $pythonArgs -NoNewWindow -Wait
        }
        'JavaScript' {
            $nodeArgs = @($func.Path) + $Arguments
            Start-Process -FilePath "node" -ArgumentList $nodeArgs -NoNewWindow -Wait
        }
        default {
            Write-Error "Cannot invoke function of type $($func.Type)."
        }
    }
}

Export-ModuleMember -Function Load-EmbeddedFunctions, Invoke-EmbeddedFunction