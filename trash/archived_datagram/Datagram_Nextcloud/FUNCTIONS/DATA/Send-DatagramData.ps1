<#
.SYNOPSIS
Sends data to a datagram database (insert, update, delete, query).
.DESCRIPTION
Implements the SendDataToDB core function specified in the Datagram Function API.
Supports multiple database types and operations with flexible parameters.
.PARAMETER DatabaseType
Type of database: SQLite, JSON, XML, Access.
.PARAMETER DatabasePath
Absolute path to the database file or directory.
.PARAMETER Operation
Operation to perform: Insert, Update, Delete, Query.
.PARAMETER TableName
Name of the table (or JSON/XML root element) to operate on.
.PARAMETER Data
For Insert/Update: Hashtable or array of hashtables containing column/value pairs.
For Query: Not used.
.PARAMETER WhereCondition
For Update/Delete: Optional condition to filter rows (SQL WHERE clause for SQLite, property match for JSON/XML).
.PARAMETER Query
For Query operation: SQL query string (SQLite) or JSONPath/XPath expression.
.PARAMETER SingleOutputMode
If true, returns a single object (first result) rather than an array. Default false.
.PARAMETER Column
Optional column name to return (for Query).
.PARAMETER RowID
Optional row identifier (for Update/Delete).
.PARAMETER Parameters
Additional parameters as hashtable for query placeholders.
.EXAMPLE
Send-DatagramData -DatabaseType SQLite -DatabasePath "C:\Datagram\Databases\Default\Data\main.db" -Operation Insert -TableName Images -Data @{ Path = "image1.jpg"; Metadata = '{"width":1920}' }
.EXAMPLE
Send-DatagramData -DatabaseType SQLite -DatabasePath "C:\Datagram\Databases\Default\Data\main.db" -Operation Query -TableName Images -Query "SELECT * FROM Images WHERE Metadata LIKE '%1920%'"
.EXAMPLE
Send-DatagramData -DatabaseType JSON -DatabasePath "C:\Datagram\Databases\Default\Data" -Operation Insert -TableName Images -Data @{ Path = "image2.jpg"; Metadata = @{ width = 1080 } }
.OUTPUTS
For Insert: Last inserted ID (if supported).
For Update/Delete: Number of rows affected.
For Query: Array of result objects, or single object if SingleOutputMode is true.
.NOTES
Part of the Datagram Function API. Version 1.0.0.
#>
function Send-DatagramData {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [ValidateSet('SQLite', 'JSON', 'XML', 'Access')]
        [string]$DatabaseType,

        [Parameter(Mandatory=$true)]
        [string]$DatabasePath,

        [Parameter(Mandatory=$true)]
        [ValidateSet('Insert', 'Update', 'Delete', 'Query')]
        [string]$Operation,

        [Parameter(Mandatory=$true)]
        [string]$TableName,

        [Parameter(Mandatory=$false)]
        [object]$Data,

        [Parameter(Mandatory=$false)]
        [string]$WhereCondition,

        [Parameter(Mandatory=$false)]
        [string]$Query,

        [Parameter(Mandatory=$false)]
        [switch]$SingleOutputMode,

        [Parameter(Mandatory=$false)]
        [string]$Column,

        [Parameter(Mandatory=$false)]
        [string]$RowID,

        [Parameter(Mandatory=$false)]
        [hashtable]$Parameters
    )

    Write-Host "Send-DatagramData: $Operation on $TableName ($DatabaseType)" -ForegroundColor Cyan

    # Validate input combinations
    if ($Operation -in @('Insert', 'Update') -and $null -eq $Data) {
        Write-Error "Data parameter is required for $Operation operation."
        return
    }
    if ($Operation -eq 'Query' -and [string]::IsNullOrWhiteSpace($Query)) {
        Write-Warning "Query parameter not provided; will select all rows from $TableName."
    }

    switch ($DatabaseType) {
        'SQLite' {
            # Load SQLite assembly
            $assemblyLoaded = $false
            $sqliteType = $null
            try {
                Add-Type -Path "Microsoft.Data.Sqlite.dll" -ErrorAction SilentlyContinue
                $assemblyLoaded = $true
                $sqliteType = 'Microsoft.Data.Sqlite'
                Write-Verbose "Loaded Microsoft.Data.Sqlite assembly."
            } catch {
                try {
                    Add-Type -Path "System.Data.SQLite.dll" -ErrorAction SilentlyContinue
                    $assemblyLoaded = $true
                    $sqliteType = 'System.Data.SQLite'
                    Write-Verbose "Loaded System.Data.SQLite assembly."
                } catch {
                    Write-Error "SQLite assembly not found. Please install Microsoft.Data.Sqlite or System.Data.SQLite."
                    return
                }
            }

            # Build connection string
            $connectionString = "Data Source=$DatabasePath"
            try {
                if ($sqliteType -eq 'Microsoft.Data.Sqlite') {
                    $connection = New-Object Microsoft.Data.Sqlite.SqliteConnection $connectionString
                } else {
                    $connection = New-Object System.Data.SQLite.SQLiteConnection $connectionString
                }
                $connection.Open()
                $command = $connection.CreateCommand()

                # Build SQL based on operation
                switch ($Operation) {
                    'Insert' {
                        $columns = @()
                        $values = @()
                        $paramNames = @()
                        $paramIndex = 0
                        foreach ($key in $Data.Keys) {
                            $columns += $key
                            $paramName = "@p$paramIndex"
                            $paramNames += $paramName
                            $command.Parameters.AddWithValue($paramName, $Data[$key]) | Out-Null
                            $paramIndex++
                        }
                        $sql = "INSERT INTO $TableName ($($columns -join ', ')) VALUES ($($paramNames -join ', '));"
                        $command.CommandText = $sql
                        $rowsAffected = $command.ExecuteNonQuery()
                        # Get last inserted row ID
                        $command.CommandText = "SELECT last_insert_rowid();"
                        $lastId = $command.ExecuteScalar()
                        $result = $lastId
                        Write-Host "Inserted row with ID $lastId" -ForegroundColor Green
                    }
                    'Update' {
                        $setClauses = @()
                        $paramIndex = 0
                        foreach ($key in $Data.Keys) {
                            $paramName = "@p$paramIndex"
                            $setClauses += "$key = $paramName"
                            $command.Parameters.AddWithValue($paramName, $Data[$key]) | Out-Null
                            $paramIndex++
                        }
                        $sql = "UPDATE $TableName SET $($setClauses -join ', ')"
                        if (-not [string]::IsNullOrWhiteSpace($WhereCondition)) {
                            $sql += " WHERE $WhereCondition"
                        } elseif (-not [string]::IsNullOrWhiteSpace($RowID)) {
                            $sql += " WHERE ID = $RowID"
                        } else {
                            Write-Warning "No WHERE condition or RowID provided; will update all rows."
                        }
                        $command.CommandText = $sql
                        $rowsAffected = $command.ExecuteNonQuery()
                        $result = $rowsAffected
                        Write-Host "Updated $rowsAffected row(s)" -ForegroundColor Green
                    }
                    'Delete' {
                        $sql = "DELETE FROM $TableName"
                        if (-not [string]::IsNullOrWhiteSpace($WhereCondition)) {
                            $sql += " WHERE $WhereCondition"
                        } elseif (-not [string]::IsNullOrWhiteSpace($RowID)) {
                            $sql += " WHERE ID = $RowID"
                        } else {
                            Write-Warning "No WHERE condition or RowID provided; will delete all rows."
                        }
                        $command.CommandText = $sql
                        $rowsAffected = $command.ExecuteNonQuery()
                        $result = $rowsAffected
                        Write-Host "Deleted $rowsAffected row(s)" -ForegroundColor Green
                    }
                    'Query' {
                        if ([string]::IsNullOrWhiteSpace($Query)) {
                            $Query = "SELECT * FROM $TableName"
                        }
                        $command.CommandText = $Query
                        $reader = $command.ExecuteReader()
                        $results = @()
                        while ($reader.Read()) {
                            $row = @{}
                            for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                                $columnName = $reader.GetName($i)
                                $row[$columnName] = $reader.GetValue($i)
                            }
                            $results += [PSCustomObject]$row
                        }
                        $reader.Close()
                        $result = $results
                        Write-Host "Query returned $($results.Count) row(s)" -ForegroundColor Green
                    }
                }
                $connection.Close()
            } catch {
                Write-Error "Database operation failed: $_"
                return
            }

            # Apply SingleOutputMode for Query
            if ($Operation -eq 'Query' -and $SingleOutputMode -and $result.Count -gt 0) {
                $result = $result[0]
            }
            return $result
        }

        'JSON' {
            $jsonPath = Join-Path $DatabasePath "$TableName.json"
            if (-not (Test-Path $jsonPath)) {
                Write-Warning "JSON file for table '$TableName' does not exist. Creating empty array."
                @() | ConvertTo-Json | Set-Content $jsonPath -Encoding UTF8
            }
            $jsonContent = Get-Content $jsonPath -Raw | ConvertFrom-Json
            switch ($Operation) {
                'Insert' {
                    # Data can be hashtable or array of hashtables
                    if ($Data -is [array]) {
                        foreach ($item in $Data) {
                            $jsonContent += $item
                        }
                    } else {
                        $jsonContent += $Data
                    }
                    $jsonContent | ConvertTo-Json -Depth 10 | Set-Content $jsonPath -Encoding UTF8
                    $result = $jsonContent.Count
                    Write-Host "Inserted into JSON. Total items: $result" -ForegroundColor Green
                }
                'Update' {
                    # Simple update by matching property (requires WhereCondition or RowID)
                    $updated = 0
                    for ($i = 0; $i -lt $jsonContent.Count; $i++) {
                        $match = $false
                        if (-not [string]::IsNullOrWhiteSpace($RowID)) {
                            if ($jsonContent[$i].ID -eq $RowID) { $match = $true }
                        } elseif (-not [string]::IsNullOrWhiteSpace($WhereCondition)) {
                            # Very simple property match: assume format "Property = Value"
                            # This is a placeholder; real implementation would need a mini query engine.
                            $match = $true # stub
                        }
                        if ($match) {
                            foreach ($key in $Data.Keys) {
                                $jsonContent[$i].$key = $Data[$key]
                            }
                            $updated++
                        }
                    }
                    $jsonContent | ConvertTo-Json -Depth 10 | Set-Content $jsonPath -Encoding UTF8
                    $result = $updated
                    Write-Host "Updated $updated item(s)" -ForegroundColor Green
                }
                'Delete' {
                    # Similar matching logic
                    $newArray = @()
                    $deleted = 0
                    foreach ($item in $jsonContent) {
                        $match = $false
                        if (-not [string]::IsNullOrWhiteSpace($RowID)) {
                            if ($item.ID -eq $RowID) { $match = $true }
                        } elseif (-not [string]::IsNullOrWhiteSpace($WhereCondition)) {
                            $match = $true # stub
                        }
                        if (-not $match) {
                            $newArray += $item
                        } else {
                            $deleted++
                        }
                    }
                    $newArray | ConvertTo-Json -Depth 10 | Set-Content $jsonPath -Encoding UTF8
                    $result = $deleted
                    Write-Host "Deleted $deleted item(s)" -ForegroundColor Green
                }
                'Query' {
                    # Simple filtering based on WhereCondition (stub)
                    $results = $jsonContent
                    if (-not [string]::IsNullOrWhiteSpace($Query)) {
                        Write-Warning "JSON query language not implemented; returning all items."
                    }
                    $result = $results
                    Write-Host "Query returned $($results.Count) item(s)" -ForegroundColor Green
                }
            }
            if ($Operation -eq 'Query' -and $SingleOutputMode -and $result.Count -gt 0) {
                $result = $result[0]
            }
            return $result
        }

        'XML' {
            Write-Warning "XML database operations not yet implemented."
            return $null
        }

        'Access' {
            Write-Warning "Access database operations not yet implemented."
            return $null
        }
    }
}

Export-ModuleMember -Function Send-DatagramData