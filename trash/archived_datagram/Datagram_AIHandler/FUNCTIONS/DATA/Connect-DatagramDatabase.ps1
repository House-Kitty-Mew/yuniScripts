# Connect-DatagramDatabase.ps1
# Connects to datagram database referenced in GUI configuration

function Connect-DatagramDatabase {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [string]$DatagramPath,
        
        [string]$DatabaseName = "Default"
    )

    $dbPath = Join-Path $DatagramPath "Databases\$DatabaseName\Data"
    if (-not (Test-Path $dbPath)) {
        Write-Warning "Database path not found: $dbPath"
        return $null
    }

    Write-Host "Connecting to datagram database: $DatabaseName" -ForegroundColor Cyan

    # Determine database type by examining files
    $dbFiles = Get-ChildItem -Path $dbPath -File
    $dbType = 'Unknown'
    $connection = $null

    foreach ($file in $dbFiles) {
        switch ($file.Extension.ToLower()) {
            '.db' { $dbType = 'SQLite'; break }
            '.sqlite' { $dbType = 'SQLite'; break }
            '.mdb' { $dbType = 'Access'; break }
            '.accdb' { $dbType = 'Access'; break }
            '.json' { $dbType = 'JSON'; break }
            '.xml' { $dbType = 'XML'; break }
        }
        if ($dbType -ne 'Unknown') { break }
    }

    Write-Host "Detected database type: $dbType" -ForegroundColor Yellow

    switch ($dbType) {
        'SQLite' {
            $connection = Connect-SQLiteDatabase -Path $dbPath
        }
        'Access' {
            $connection = Connect-AccessDatabase -Path $dbPath
        }
        'JSON' {
            $connection = Connect-JsonDatabase -Path $dbPath
        }
        'XML' {
            $connection = Connect-XmlDatabase -Path $dbPath
        }
        default {
            Write-Warning "Unsupported database type: $dbType"
        }
    }

    return $connection
}

function Connect-SQLiteDatabase {
    param([string]$Path)
    
    Write-Host "Connecting to SQLite database..." -ForegroundColor Cyan
    
    # Look for SQLite database file
    $dbFile = Get-ChildItem -Path $Path -Filter *.db, *.sqlite | Select-Object -First 1
    if (-not $dbFile) {
        Write-Warning "No SQLite database file found in $Path"
        return $null
    }
    
    $fullPath = $dbFile.FullName
    
    # Try to load Microsoft.Data.Sqlite (for .NET Core / PowerShell 7+)
    $sqliteLoaded = $false
    try {
        Add-Type -Path "Microsoft.Data.Sqlite.dll" -ErrorAction SilentlyContinue
        $sqliteLoaded = $true
        Write-Host "Loaded Microsoft.Data.Sqlite assembly." -ForegroundColor Green
    } catch {
        # Try System.Data.SQLite
        try {
            Add-Type -Path "System.Data.SQLite.dll" -ErrorAction SilentlyContinue
            $sqliteLoaded = $true
            Write-Host "Loaded System.Data.SQLite assembly." -ForegroundColor Green
        } catch {
            Write-Warning "SQLite assembly not found. Please install either Microsoft.Data.Sqlite (for .NET Core) or System.Data.SQLite (for .NET Framework)."
        }
    }
    
    if ($sqliteLoaded) {
        # Create connection object
        $connection = [PSCustomObject]@{
            Type = 'SQLite'
            Path = $fullPath
            IsConnected = $true
            Connection = $null  # Will be set when opening connection
        }
        # TODO: Open connection on demand
        Write-Host "SQLite connection ready (placeholder)." -ForegroundColor Yellow
        return $connection
    } else {
        # Return placeholder with instructions
        Write-Warning "SQLite connection requires assembly. Please download SQLite interop DLLs."
        return [PSCustomObject]@{
            Type = 'SQLite'
            Path = $fullPath
            IsConnected = $false
        }
    }
}

function Connect-AccessDatabase {
    param([string]$Path)
    
    Write-Host "Connecting to Access database..." -ForegroundColor Cyan
    
    # Look for Access database file
    $dbFile = Get-ChildItem -Path $Path -Filter *.mdb, *.accdb | Select-Object -First 1
    if (-not $dbFile) {
        Write-Warning "No Access database file found in $Path"
        return $null
    }
    
    $fullPath = $dbFile.FullName
    
    # Check if OLEDB provider is available
    $oledbAvailable = $false
    try {
        $conn = New-Object System.Data.OleDb.OleDbConnection
        $oledbAvailable = $true
    } catch {
        Write-Warning "OLEDB provider not available. Access database connectivity requires Microsoft Access Database Engine."
    }
    
    if ($oledbAvailable) {
        # Create connection object
        $connection = [PSCustomObject]@{
            Type = 'Access'
            Path = $fullPath
            IsConnected = $false
            ConnectionString = "Provider=Microsoft.ACE.OLEDB.12.0;Data Source=$fullPath"
        }
        Write-Host "Access connection ready (OLEDB provider detected)." -ForegroundColor Yellow
        return $connection
    } else {
        Write-Warning "Access database connection requires Microsoft Access Database Engine (ACE OLEDB)."
        return [PSCustomObject]@{
            Type = 'Access'
            Path = $fullPath
            IsConnected = $false
        }
    }
}

function Connect-JsonDatabase {
    param([string]$Path)
    Write-Host "Loading JSON database files..." -ForegroundColor Cyan
    $data = @{}
    $jsonFiles = Get-ChildItem -Path $Path -Filter *.json
    foreach ($file in $jsonFiles) {
        $content = Get-Content $file.FullName -Raw | ConvertFrom-Json
        $data[$file.BaseName] = $content
    }
    return [PSCustomObject]@{
        Type = 'JSON'
        Path = $Path
        Data = $data
    }
}

function Connect-XmlDatabase {
    param([string]$Path)
    Write-Host "Loading XML database files..." -ForegroundColor Cyan
    $data = @{}
    $xmlFiles = Get-ChildItem -Path $Path -Filter *.xml
    foreach ($file in $xmlFiles) {
        $xml = [xml](Get-Content $file.FullName)
        $data[$file.BaseName] = $xml
    }
    return [PSCustomObject]@{
        Type = 'XML'
        Path = $Path
        Data = $data
    }
}

function Query-DatagramDatabase {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [object]$Connection,
        
        [Parameter(Mandatory=$true)]
        [string]$Query,
        
        [hashtable]$Parameters
    )

    Write-Host "Executing query: $Query" -ForegroundColor Cyan

    switch ($Connection.Type) {
        'SQLite' {
            if (-not $Connection.IsConnected) {
                Write-Warning "SQLite connection not established. Please ensure SQLite assembly is available."
                return @()
            }
            # TODO: Execute SQLite query using Microsoft.Data.Sqlite or System.Data.SQLite
            Write-Warning "SQLite query execution not fully implemented (placeholder)."
            # If connection.Connection is not null, we could run query here
            return @()
        }
        'JSON' {
            # Simple JSON query: treat as object path
            # This is a naive implementation; real usage would need a proper query language
            $result = $Connection.Data | Select-Object -ExpandProperty * | Where-Object { $_ -match $Query }
            return $result
        }
        'XML' {
            # XPath query? For now, just search
            Write-Warning "XML query not implemented."
            return @()
        }
        default {
            Write-Error "Unsupported connection type: $($Connection.Type)"
            return @()
        }
    }
}

Export-ModuleMember -Function Connect-DatagramDatabase, Connect-SQLiteDatabase, Connect-AccessDatabase, Connect-JsonDatabase, Connect-XmlDatabase, Query-DatagramDatabase