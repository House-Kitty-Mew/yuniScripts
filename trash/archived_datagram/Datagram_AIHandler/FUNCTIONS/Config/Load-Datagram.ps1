# Load-Datagram.ps1
# Loads and validates a datagram structure

function Load-Datagram {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$Path
    )

    # Ensure path exists
    if (-not (Test-Path $Path)) {
        Write-Error "Datagram path not found: $Path"
        return $null
    }

    # Normalize path
    $datagramRoot = (Resolve-Path $Path).Path

    Write-Host "Loading datagram from: $datagramRoot" -ForegroundColor Cyan

    # 1. Read Base.ini
    $baseIniPath = Join-Path $datagramRoot "Meta\Base.ini"
    if (-not (Test-Path $baseIniPath)) {
        Write-Error "Base.ini not found in datagram meta folder"
        return $null
    }

    $baseConfig = Parse-DatagramIni $baseIniPath

    # 2. Validate hash (optional - can be skipped)
    $hashValid = Test-DatagramHash -Path $datagramRoot -ExpectedHash $baseConfig.'Datagram Hash UQID'
    if (-not $hashValid) {
        Write-Warning "Datagram hash validation failed! The datagram may have been tampered with."
        # Depending on policy, we could abort here
    }

    # 3. Load metadata
    $metaIniPath = Join-Path $datagramRoot "Meta\DatagramMeta.ini"
    $metaConfig = @{}
    if (Test-Path $metaIniPath) {
        $metaConfig = Parse-DatagramIni $metaIniPath
    }

    # 4. Load function version requirements
    $funcIniPath = Join-Path $datagramRoot "Meta\FunctionsReqVersions.ini"
    $funcVersions = @{}
    if (Test-Path $funcIniPath) {
        $funcVersions = Parse-DatagramIni $funcIniPath
    }

    # 5. Load GUI definition
    $guiIniPath = Join-Path $datagramRoot "PreLoad\Gui\Default_Gui.ini"
    $guiConfig = @{}
    if (Test-Path $guiIniPath) {
        $guiConfig = Parse-DatagramIni $guiIniPath
    }

    # 6. Construct datagram object
    $datagram = [PSCustomObject]@{
        RootPath = $datagramRoot
        Version = $baseConfig.'Datagram Version'
        Name = $baseConfig.'Datagram NAME ID'
        Author = $baseConfig.'Datagram Author'
        HashAlgorithm = $baseConfig.'Datagram Hashing Algo'
        Hash = $baseConfig.'Datagram Hash UQID'
        Encryption = $baseConfig.Encryption
        PublicKey = $baseConfig.'Encryption Public Key'
        ServerUrl = $baseConfig.'Encryption Server URL'
        Metadata = $metaConfig
        FunctionVersions = $funcVersions
        GuiConfig = $guiConfig
        HashValid = $hashValid
    }

    # 7. Test encryption configuration
    $encryptionTest = Test-DatagramEncryption -Datagram $datagram
    $datagram | Add-Member -NotePropertyName 'EncryptionValid' -NotePropertyValue $encryptionTest -Force
    $datagram | Add-Member -NotePropertyName 'Encrypted' -NotePropertyValue ($datagram.Encryption -eq '1') -Force

    Write-Host "Datagram loaded: $($datagram.Name) (v$($datagram.Version))" -ForegroundColor Green
    return $datagram
}

# Helper function to parse .ini files in datagram format (key={value} or key=value)
function Parse-DatagramIni {
    param([string]$FilePath)

    $config = @{}
    $content = Get-Content $FilePath -ErrorAction Stop
    foreach ($line in $content) {
        $trimmed = $line.Trim()
        # Skip empty lines and comments
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        
        # Match pattern [Key]={value} or [Key]=value
        if ($trimmed -match '^\[([^\]]+)\]=\{(.*)\}$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim()
            $config[$key] = $value
        } elseif ($trimmed -match '^\[([^\]]+)\]=(.*)$') {
            $key = $matches[1].Trim()
            $value = $matches[2].Trim()
            $config[$key] = $value
        } else {
            Write-Warning "Unrecognized line in INI file: $line"
        }
    }
    return $config
}

Export-ModuleMember -Function Load-Datagram, Parse-DatagramIni