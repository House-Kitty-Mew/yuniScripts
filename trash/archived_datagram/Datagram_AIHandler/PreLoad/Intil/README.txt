# Intil (Initialization) Directory
This folder holds scripts or configuration files that are executed before the GUI loads.

## Purpose
- Run setup tasks (e.g., database connection, environment checks).
- Load custom function definitions embedded in the datagram.
- Perform integrity verification beyond the base hash.
- Register custom GUI components or plugins.

## Expected Content
- PowerShell scripts (.ps1)
- Python scripts (.py)
- JavaScript/TypeScript modules (.js, .ts)
- Compiled binaries (.dll, .so)
- Configuration files (.json, .yaml)

## Execution Order
1. Scripts in `Intil/` are executed after the datagram loader validates the hash and encryption.
2. Scripts are run in alphabetical order (or as specified by a manifest).
3. If a script fails, the datagram may abort loading (depending on client policy).

## Example
A script `register_functions.ps1` could define the `ImageViewer` and `Buttons` functions required by the GUI:

```powershell
function Global:ImageViewer {
    param($screen, $imageRef)
    # Render image on screen
}

function Global:Buttons {
    param($buttonId, $action)
    if ($action -eq 'ExitAppSafely') { Exit-Prompt }
}
```

## Notes
- This is a template directory; replace this README with your actual initialization scripts.
- Empty by default.

---
Datagram Specification v1.0.0