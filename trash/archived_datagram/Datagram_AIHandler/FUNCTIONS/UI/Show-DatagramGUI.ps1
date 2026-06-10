function Show-DatagramGUI {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)]
        [PSCustomObject]$Datagram
    )

    Write-Host "Rendering Datagram GUI for: $($Datagram.Name)" -ForegroundColor Cyan

    if (-not $Datagram.GuiConfig -or $Datagram.GuiConfig.Count -eq 0) {
        Write-Warning "No GUI configuration found. Cannot render GUI."
        return
    }

    # Extract GUI components from configuration
    $gui = $Datagram.GuiConfig

    # Display GUI summary
    Write-Host "=== Datagram GUI Summary ===" -ForegroundColor Green
    foreach ($key in $gui.Keys) {
        Write-Host "$key = $($gui[$key])" -ForegroundColor Yellow
    }

    # TODO: Parse GUI elements and render actual UI
    # The GUI specification may include:
    # - Images (referenced by metadata keys)
    # - Buttons with actions (linked to embedded functions)
    # - Text labels
    # - Database-driven content (DB:Default)
    
    # For now, we'll create a simple Windows Forms UI as a proof-of-concept.
    # This stub will just show a message box with datagram info.
    
    $guiType = if ($gui.ContainsKey('GuiType')) { $gui['GuiType'] } else { 'Simple' }
    
    if ($guiType -eq 'Simple') {
        Write-Host "Launching simple GUI preview..." -ForegroundColor Cyan
        Show-SimpleDatagramPreview -Datagram $Datagram
    } else {
        Write-Warning "GUI type '$guiType' not yet implemented."
    }
}
function Show-SimpleDatagramPreview {
    param([PSCustomObject]$Datagram)

    # Load Windows Forms assembly
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Datagram: $($Datagram.Name)"
    $form.Size = New-Object System.Drawing.Size(600, 400)
    $form.StartPosition = 'CenterScreen'

    # Add a label
    $label = New-Object System.Windows.Forms.Label
    $label.Location = New-Object System.Drawing.Point(20, 20)
    $label.Size = New-Object System.Drawing.Size(550, 100)
    $label.Text = "Datagram: $($Datagram.Name)`nVersion: $($Datagram.Version)`nAuthor: $($Datagram.Author)`n`nThis is a placeholder GUI. Real GUI would display images, buttons, and database content as defined in Default_Gui.ini."
    $form.Controls.Add($label)

    # Add a button to close
    $button = New-Object System.Windows.Forms.Button
    $button.Location = New-Object System.Drawing.Point(250, 300)
    $button.Size = New-Object System.Drawing.Size(100, 30)
    $button.Text = "Close"
    $button.Add_Click({ $form.Close() })
    $form.Controls.Add($button)

    # Show form (modal)
    $form.ShowDialog() | Out-Null
}

function Parse-GuiElements {
    [CmdletBinding()]
    param([hashtable]$GuiConfig)
    Write-Verbose "Parsing GUI elements (stub)."
    return [PSCustomObject]@{
        Screens = @()
        Buttons = @()
        Images = @()
        StartDisplay = ""
        Raw = $GuiConfig
    }
}

Export-ModuleMember -Function Show-DatagramGUI, Show-SimpleDatagramPreview, Parse-GuiElements
