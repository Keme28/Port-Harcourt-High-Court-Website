# ==============================================================================
# High Court 6 File Portal - Adaptive Startup Script
# Automatically configures, verifies, launches the backend, and opens the browser.
# Compatible across different Windows PCs and environments.
# ==============================================================================

$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $ScriptDir "app"
$Requirements = Join-Path $AppDir "requirements.txt"
$EnvDir = Join-Path $AppDir "env"
$EnvPython = Join-Path $EnvDir "Scripts\python.exe"
$EnvPip = Join-Path $EnvDir "Scripts\pip.exe"
$DataDir = Join-Path $AppDir "data"
if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
}
$LogFile = Join-Path $DataDir "portal_startup.log"
$HostAddr = "127.0.0.1"
$Port = 8765
$Url = "http://${HostAddr}:${Port}"
$PngIcon = Join-Path $ScriptDir "icon.png"
$IcoIcon = Join-Path $ScriptDir "icon.ico"
$ExeLauncher = Join-Path $ScriptDir "High Court Portal.exe"

Write-Host "--------------------------------------------------------" -ForegroundColor Cyan
Write-Host "           High Court 6 File Portal Launcher            " -ForegroundColor Cyan
Write-Host "--------------------------------------------------------" -ForegroundColor Cyan

# ------------------------------------------------------------------------------
# 1. Helper function: Test if the portal is already running
# ------------------------------------------------------------------------------
function Test-PortalHealth {
    param([string]$CheckUrl = "$Url/api/health")
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $CheckUrl -TimeoutSec 2 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            return $true
        }
    } catch {
        # Not reachable yet or different response
    }
    return $false
}

# ------------------------------------------------------------------------------
# 2. Helper function: Test if a Python binary is functional (>= Python 3.8)
# ------------------------------------------------------------------------------
function Test-PythonExecutable {
    param([string]$ExePath)
    if (-not $ExePath -or -not (Test-Path $ExePath)) {
        return $false
    }
    try {
        $check = & "$ExePath" -c "import sys; assert sys.version_info >= (3, 8); print('VALID_PYTHON')" 2>$null
        if ($check -and $check.Trim() -eq "VALID_PYTHON") {
            return $true
        }
    } catch {
        return $false
    }
    return $false
}

# ------------------------------------------------------------------------------
# 3. Helper function: Test if the virtual environment can import the app modules
# ------------------------------------------------------------------------------
function Test-EnvHealth {
    param([string]$PyPath)
    if (-not (Test-PythonExecutable $PyPath)) {
        return $false
    }
    try {
        $testCmd = "import sys; sys.path.insert(0, r'$AppDir'); import fastapi, uvicorn, openpyxl, jinja2, pydantic, auth, db, logic, store; print('ENV_OK')"
        $result = & "$PyPath" -c $testCmd 2>$null
        if ($result -and $result.Trim() -eq "ENV_OK") {
            return $true
        }
    } catch {
        return $false
    }
    return $false
}

# ------------------------------------------------------------------------------
# 4. Helper function: Ensure icon.ico and shortcuts are ready
# ------------------------------------------------------------------------------
function Ensure-IconsAndShortcuts {
    # Generate icon.ico if missing
    if ((-not (Test-Path $IcoIcon)) -and (Test-Path $PngIcon)) {
        try {
            Add-Type -AssemblyName System.Drawing -ErrorAction SilentlyContinue
            $img = [System.Drawing.Image]::FromFile($PngIcon)
            $sizes = @(256, 128, 64, 48, 32, 16)
            $pngStreams = @()
            foreach ($sz in $sizes) {
                $bmp = New-Object System.Drawing.Bitmap $sz, $sz
                $g = [System.Drawing.Graphics]::FromImage($bmp)
                $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
                $g.DrawImage($img, 0, 0, $sz, $sz)
                $g.Dispose()
                $ms = New-Object System.IO.MemoryStream
                $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
                $bmp.Dispose()
                $pngStreams += $ms
            }
            $img.Dispose()

            $fs = [System.IO.File]::Create($IcoIcon)
            $bw = New-Object System.IO.BinaryWriter $fs
            $bw.Write([uint16]0)
            $bw.Write([uint16]1)
            $bw.Write([uint16]$sizes.Count)
            $offset = 6 + ($sizes.Count * 16)
            for ($i = 0; $i -lt $sizes.Count; $i++) {
                $sz = $sizes[$i]
                $w = if ($sz -ge 256) { [byte]0 } else { [byte]$sz }
                $h = if ($sz -ge 256) { [byte]0 } else { [byte]$sz }
                $bytes = $pngStreams[$i].ToArray()
                $bw.Write($w)
                $bw.Write($h)
                $bw.Write([byte]0)
                $bw.Write([byte]0)
                $bw.Write([uint16]1)
                $bw.Write([uint16]32)
                $bw.Write([uint32]$bytes.Length)
                $bw.Write([uint32]$offset)
                $offset += $bytes.Length
            }
            for ($i = 0; $i -lt $sizes.Count; $i++) {
                $bw.Write($pngStreams[$i].ToArray())
                $pngStreams[$i].Dispose()
            }
            $bw.Close()
            $fs.Close()
        } catch {}
    }

    # Ensure desktop shortcut exists and points to current folder
    try {
        $desktop = [Environment]::GetFolderPath("Desktop")
        if ($desktop -and (Test-Path $desktop)) {
            $desktopLnk = Join-Path $desktop "High Court Portal.lnk"
            $target = if (Test-Path $ExeLauncher) { $ExeLauncher } else { (Join-Path $ScriptDir "Open-HCT6.cmd") }
            $WshShell = New-Object -ComObject WScript.Shell
            $Shortcut = $WshShell.CreateShortcut($desktopLnk)
            $Shortcut.TargetPath = $target
            $Shortcut.WorkingDirectory = $ScriptDir
            if (Test-Path $IcoIcon) { $Shortcut.IconLocation = "$IcoIcon,0" }
            $Shortcut.Description = "High Court 6 File Portal"
            $Shortcut.Save()
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($WshShell) | Out-Null
        }
    } catch {}
}

# ------------------------------------------------------------------------------
# 5. Helper function: Find a working host Python on this PC
# ------------------------------------------------------------------------------
function Find-HostPython {
    Write-Host "Searching for installed Python on this PC..." -ForegroundColor Gray

    $candidates = [System.Collections.Generic.List[string]]::new()

    # A. Active Python in current session / virtualenv
    if ($env:PYTHON_EXECUTABLE) { $candidates.Add($env:PYTHON_EXECUTABLE) }
    if ($env:VIRTUAL_ENV) { $candidates.Add((Join-Path $env:VIRTUAL_ENV "Scripts\python.exe")) }
    if ($env:CONDA_PREFIX) { $candidates.Add((Join-Path $env:CONDA_PREFIX "python.exe")) }

    # B. Official Python Launcher (py.exe) with version checks
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $pyVers = @("3.14", "3.13", "3.12", "3.11", "3.10", "3.9", "3.8", "3")
        foreach ($v in $pyVers) {
            try {
                $p = & py -$v -c "import sys; print(sys.executable)" 2>$null
                if ($p -and (Test-Path $p.Trim())) { $candidates.Add($p.Trim()) }
            } catch {}
        }
        try {
            $pyPaths = & py -0p 2>$null
            if ($pyPaths) {
                foreach ($line in ($pyPaths -split "`r?`n")) {
                    if ($line -match '^\s*-\S+\s+(.+)$') {
                        $p = $matches[1].Trim()
                        if ($p -and (Test-Path $p)) { $candidates.Add($p) }
                    }
                }
            }
        } catch {}
    }

    # C. PATH python.exe and python3.exe (excluding WindowsApps dummy redirectors if broken)
    $pathPythons = Get-Command python.exe, python3.exe -All -ErrorAction SilentlyContinue
    if ($pathPythons) {
        foreach ($cmd in $pathPythons) {
            $src = $cmd.Source
            if ($src -and ($src -notmatch "WindowsApps")) {
                $candidates.Add($src)
            }
        }
    }

    # D. Common Windows installation directories across all versions
    $commonSearchPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
        "$env:LOCALAPPDATA\Python\pythoncore-*\python.exe",
        "$env:LOCALAPPDATA\Python\bin\python.exe",
        "C:\Program Files\Python3*\python.exe",
        "C:\Program Files (x86)\Python3*\python.exe",
        "C:\Python3*\python.exe",
        "C:\Python*\python.exe",
        "$env:USERPROFILE\anaconda3\python.exe",
        "$env:USERPROFILE\miniconda3\python.exe",
        "$env:ALLUSERSPROFILE\anaconda3\python.exe",
        "$env:ALLUSERSPROFILE\miniconda3\python.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\*\python.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\python.exe",
        "$env:USERPROFILE\scoop\shims\python.exe",
        "C:\ProgramData\chocolatey\bin\python.exe"
    )

    foreach ($pattern in $commonSearchPaths) {
        $found = Get-Item -Path $pattern -ErrorAction SilentlyContinue
        if ($found) {
            foreach ($item in $found) {
                $candidates.Add($item.FullName)
            }
        }
    }

    # E. Check WindowsApps python as last fallback if it actually executes
    if ($pathPythons) {
        foreach ($cmd in $pathPythons) {
            $src = $cmd.Source
            if ($src -and ($src -match "WindowsApps")) {
                $candidates.Add($src)
            }
        }
    }

    # Iterate through candidates and return the first verified working Python
    foreach ($cand in $candidates) {
        if ($cand -and (Test-PythonExecutable $cand)) {
            return $cand
        }
    }

    return $null
}

# ------------------------------------------------------------------------------
# 6. Check if the portal is already running
# ------------------------------------------------------------------------------
Ensure-IconsAndShortcuts

$LoginUrl = "$Url/login"

if (Test-PortalHealth) {
    Write-Host "[OK] High Court Portal is already running at $Url" -ForegroundColor Green
    Write-Host "Opening login page..." -ForegroundColor Yellow
    Start-Process $LoginUrl
    Write-Host "Ready!" -ForegroundColor Green
    exit 0
}

# ------------------------------------------------------------------------------
# 7. Verify or self-heal the Python environment
# ------------------------------------------------------------------------------
$envHealthy = Test-EnvHealth $EnvPython

if (-not $envHealthy) {
    if (Test-Path $EnvPython) {
        Write-Host "[!] Existing virtual environment is invalid or mismatched for this PC." -ForegroundColor Yellow
        Write-Host "    (This is normal when copying the project between different computers)." -ForegroundColor Gray
    } else {
        Write-Host "[i] No local virtual environment found. Setting up..." -ForegroundColor Yellow
    }

    $hostPython = Find-HostPython
    if (-not $hostPython) {
        Write-Host "`n[ERROR] No working Python installation (version 3.8 or newer) was detected on this PC." -ForegroundColor Red
        Write-Host "Please install Python from https://www.python.org/downloads/ (check 'Add Python to PATH' during installation)" -ForegroundColor Yellow
        Write-Host "or run in PowerShell: winget install Python.Python.3.12" -ForegroundColor Cyan
        Write-Host ""
        exit 1
    }

    Write-Host "[OK] Using host Python at: $hostPython" -ForegroundColor Green

    # Recreate virtual environment cleanly
    Write-Host "Creating virtual environment at $EnvDir..." -ForegroundColor Cyan
    if (Test-Path $EnvDir) {
        try {
            Remove-Item -Path $EnvDir -Recurse -Force -ErrorAction SilentlyContinue
        } catch {}
    }

    & "$hostPython" -m venv "$EnvDir"
    if (-not (Test-PythonExecutable $EnvPython)) {
        & "$hostPython" -m venv "$EnvDir" --clear
    }

    if (-not (Test-PythonExecutable $EnvPython)) {
        Write-Host "[ERROR] Failed to create virtual environment with $hostPython" -ForegroundColor Red
        exit 1
    }

    Write-Host "Installing dependencies from requirements.txt..." -ForegroundColor Cyan
    & "$EnvPython" -m pip install --upgrade pip --quiet --disable-pip-version-check
    & "$EnvPython" -m pip install -r "$Requirements" --disable-pip-version-check

    if (-not (Test-EnvHealth $EnvPython)) {
        Write-Host "[WARNING] Re-verifying module imports..." -ForegroundColor Yellow
        & "$EnvPython" -m pip install --force-reinstall -r "$Requirements" --disable-pip-version-check
    }

    Write-Host "[OK] Python environment is ready and verified." -ForegroundColor Green
} else {
    Write-Host "[OK] Python environment verified." -ForegroundColor Green
}

# ------------------------------------------------------------------------------
# 8. Start the Uvicorn server in a detached background process
# ------------------------------------------------------------------------------
Write-Host "Starting High Court Portal backend server..." -ForegroundColor Cyan

# Prepare clean startup log
"=== High Court Portal Startup Log $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $LogFile -Encoding UTF8 -Force

# Launch server with cmd.exe redirecting output to log file
$argList = "/c `"`"$EnvPython`" -m uvicorn app:app --host $HostAddr --port $Port >> `"$LogFile`" 2>&1`""
Start-Process -FilePath "cmd.exe" -ArgumentList $argList -WorkingDirectory $AppDir -WindowStyle Hidden

# ------------------------------------------------------------------------------
# 9. Wait for the server to become healthy and responsive
# ------------------------------------------------------------------------------
Write-Host "Waiting for portal to initialize..." -NoNewline -ForegroundColor Gray

$isReady = $false
$maxSeconds = 25

for ($i = 0; $i -lt ($maxSeconds * 2); $i++) {
    Start-Sleep -Milliseconds 500
    Write-Host "." -NoNewline -ForegroundColor Gray
    if (Test-PortalHealth) {
        $isReady = $true
        break
    }
}

Write-Host ""

if ($isReady) {
    Write-Host "[OK] High Court Portal is online at $Url" -ForegroundColor Green
    Write-Host "Opening login page in browser..." -ForegroundColor Cyan
    Start-Process $LoginUrl
    Write-Host "--------------------------------------------------------" -ForegroundColor Green
    Write-Host " Portal is ready for use! " -ForegroundColor Green
    Write-Host " (The server runs minimized in the background) " -ForegroundColor Gray
    Write-Host "--------------------------------------------------------" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n[ERROR] The portal did not become ready within $maxSeconds seconds." -ForegroundColor Red
    Write-Host "Here is the startup log:" -ForegroundColor Yellow
    Write-Host "--------------------------------------------------------" -ForegroundColor DarkGray
    if (Test-Path $LogFile) {
        Get-Content $LogFile -Tail 25
    } else {
        Write-Host "(Log file was not created)"
    }
    Write-Host "--------------------------------------------------------" -ForegroundColor DarkGray
    exit 1
}
