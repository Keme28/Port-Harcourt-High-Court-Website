# ==============================================================================
# Creates Windows Shortcuts with Custom Icon on Desktop and in Project Folder
# ==============================================================================

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootWorkspace = Split-Path -Parent $ScriptDir
$IcoPath = Join-Path $ScriptDir "icon.ico"
$ExePath = Join-Path $ScriptDir "High Court Portal.exe"
$CmdPath = Join-Path $ScriptDir "Open-HCT6.cmd"

$TargetToRun = if (Test-Path $ExePath) { $ExePath } else { $CmdPath }
$Desktop = [Environment]::GetFolderPath("Desktop")

function Make-Shortcut {
    param(
        [string]$ShortcutPath,
        [string]$Target,
        [string]$Icon,
        [string]$WorkingDir,
        [string]$Description = "High Court 6 File Portal"
    )
    try {
        $WshShell = New-Object -ComObject WScript.Shell
        $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath = $Target
        $Shortcut.WorkingDirectory = $WorkingDir
        $Shortcut.IconLocation = "$Icon,0"
        $Shortcut.Description = $Description
        $Shortcut.Save()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($WshShell) | Out-Null
        Write-Host "[OK] Created shortcut: $ShortcutPath" -ForegroundColor Green
    } catch {
        Write-Host "[WARNING] Could not create shortcut at $ShortcutPath : $_" -ForegroundColor Yellow
    }
}

# 1. Desktop Shortcut
if ($Desktop -and (Test-Path $Desktop)) {
    $desktopShortcut = Join-Path $Desktop "High Court Portal.lnk"
    Make-Shortcut -ShortcutPath $desktopShortcut -Target $TargetToRun -Icon $IcoPath -WorkingDir $ScriptDir
}

# 2. Project Folder Shortcut
$folderShortcut = Join-Path $ScriptDir "High Court Portal.lnk"
Make-Shortcut -ShortcutPath $folderShortcut -Target $TargetToRun -Icon $IcoPath -WorkingDir $ScriptDir

# 3. Root Workspace Shortcut
if ($RootWorkspace -and (Test-Path $RootWorkspace) -and ($RootWorkspace -ne $ScriptDir)) {
    $rootShortcut = Join-Path $RootWorkspace "High Court Portal.lnk"
    $rootExe = Join-Path $RootWorkspace "High Court Portal.exe"
    $rootIco = Join-Path $RootWorkspace "icon.ico"
    $rootTarget = if (Test-Path $rootExe) { $rootExe } else { $TargetToRun }
    $rootIcon = if (Test-Path $rootIco) { $rootIco } else { $IcoPath }
    Make-Shortcut -ShortcutPath $rootShortcut -Target $rootTarget -Icon $rootIcon -WorkingDir $RootWorkspace
}
