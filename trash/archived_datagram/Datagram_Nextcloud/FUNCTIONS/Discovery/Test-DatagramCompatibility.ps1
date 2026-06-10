# Test-DatagramCompatibility.ps1
# Validates forward/backward compatibility between loader and datagram

function Test-DatagramCompatibility {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [PSCustomObject]$Datagram,
        
        [hashtable]$LoaderCapabilities
    )

    Write-Host "Testing datagram compatibility..." -ForegroundColor Cyan

    # Required capabilities from FunctionsReqVersions.ini
    $required = $Datagram.FunctionVersions
    if (-not $required -or $required.Count -eq 0) {
        Write-Verbose "No function version requirements specified; assuming compatible."
        return $true
    }

    # Default loader capabilities (if not provided)
    if (-not $LoaderCapabilities) {
        $LoaderCapabilities = @{
            'Loader' = '1.0'
            'Image Viewer' = '1.0'
            'Buttons' = '1.0'
            'Encryption' = '0.0'
            'Database' = '1.0'
        }
    }

    $compatible = $true
    $missing = @()

    foreach ($key in $required.Keys) {
        $requiredVersion = $required[$key]
        $loaderVersion = $LoaderCapabilities[$key]

        if (-not $loaderVersion) {
            Write-Warning "Loader lacks capability: $key"
            $missing += $key
            $compatible = $false
            continue
        }

        $compat = Test-VersionCompatibility -Required $requiredVersion -Available $loaderVersion
        if (-not $compat) {
            Write-Warning "Version mismatch for `${key}: required $requiredVersion, loader has $loaderVersion"
            $compatible = $false
        } else {
            Write-Verbose "$key compatible ($requiredVersion <= $loaderVersion)"
        }
    }

    if ($compatible) {
        Write-Host "Datagram is compatible with this loader." -ForegroundColor Green
    } else {
        Write-Host "Datagram may not be fully compatible." -ForegroundColor Yellow
        if ($missing.Count -gt 0) {
            Write-Host "Missing capabilities: $($missing -join ', ')" -ForegroundColor Red
        }
    }

    return $compatible
}

function Test-VersionCompatibility {
    param(
        [string]$Required,
        [string]$Available
    )

    # Simple version comparison: assume semantic versioning (major.minor.patch)
    # For forward/backward compatibility, we check if Available >= Required (major same, minor >=)
    # This is a simplified rule; real compatibility may be more complex.
    
    $reqParts = $Required -split '\.'
    $availParts = $Available -split '\.'
    
    for ($i = 0; $i -lt [math]::Max($reqParts.Count, $availParts.Count); $i++) {
        $req = if ($i -lt $reqParts.Count) { [int]($reqParts[$i] -as [int]) } else { 0 }
        $avail = if ($i -lt $availParts.Count) { [int]($availParts[$i] -as [int]) } else { 0 }
        
        if ($avail -gt $req) {
            # Higher version at this level → compatible
            return $true
        }
        if ($avail -lt $req) {
            # Lower version → not compatible
            return $false
        }
        # Same version at this level, continue to next part
    }
    
    # All parts equal → compatible
    return $true
}

function Get-DatagramLoaderCapabilities {
    [CmdletBinding()]
    param()

    # Return the capabilities of this loader implementation
    # This should be updated as new features are added.
    $capabilities = @{
        'Loader' = '1.0'
        'Image Viewer' = '0.5'  # placeholder
        'Buttons' = '0.5'
        'Encryption' = '0.0'    # not implemented
        'Database' = '0.8'
        'Embedded Functions' = '0.9'
        'GUI' = '0.7'
    }

    return $capabilities
}

Export-ModuleMember -Function Test-DatagramCompatibility, Test-VersionCompatibility, Get-DatagramLoaderCapabilities