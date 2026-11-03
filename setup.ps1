# make_cdlc one-time setup for Windows 10/11. Started by setup.bat or install.ps1; safe to run again.
# No admin rights needed: everything goes into this folder or your user account.
#
# Installs (only what is missing):
#   1. Python 3.12            (for your user: winget, or the python.org installer)
#   2. A private Python environment in .venv with PyTorch (CUDA) + the audio libraries
#   3. The AI models (downloaded once, then everything works offline)
#   4. Rocksmith2014.NET source (the library behind DLC Builder)
#   5. The .NET 10 SDK (into runtime\dotnet if you don't have it) and builds the packager
#      (packager\publish\RsPackager.exe, self-contained: no .NET needed to run it)
# then checks for Wwise, which you install yourself (one-time, with a free account),
# connects the converter to the Bass Trainer browser extension (one-click "Build CDLC"),
# finds your Rocksmith dlc folder and adds the "RS2014 CDLC Maker" shortcuts.

param([switch]$Unattended)

# "Continue": native tools (pip, py, dotnet) write progress to stderr, which must not abort the
# script in Windows PowerShell 5.1. Every step checks $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is very slow with the progress bar
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$RsNetCommit = "4cf3325174e13f1dc5ce997e7663b2851c6afcce"
$TorchVersion = "2.11.0"
$TorchAudioVersion = "2.11.0"
$PythonInstallerUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK: $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   WARNING: $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "   ERROR: $text" -ForegroundColor Red; exit 1 }

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Has-Winget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function Find-Python312 {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { return @("py", "-3.12") }
    }
    $local = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $local) { return @($local) }
    return $null
}

# ---------------------------------------------------------------- 1. Python
Step "1/8 Python 3.12"
$py = Find-Python312
if (-not $py) {
    if (Has-Winget) {
        Write-Host "   Installing Python 3.12 for your user (winget)..."
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        Refresh-Path
        $py = Find-Python312
    }
    if (-not $py) {
        Write-Host "   Installing Python 3.12 for your user (python.org)..."
        $exe = Join-Path $env:TEMP "python-3.12-installer.exe"
        Invoke-WebRequest $PythonInstallerUrl -OutFile $exe -UseBasicParsing
        Start-Process $exe -ArgumentList "/quiet InstallAllUsers=0 Include_launcher=1 Include_test=0 Include_tcltk=1 PrependPath=0" -Wait
        Remove-Item $exe -ErrorAction SilentlyContinue
        Refresh-Path
        $py = Find-Python312
    }
    if (-not $py) { Fail "Python 3.12 could not be installed. Install it from python.org (for your user), then run setup again." }
}
Ok "Python 3.12"

# ---------------------------------------------------------------- 2. venv + packages
Step "2/8 Python packages (this downloads a few GB the first time)"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    if ($py.Count -gt 1) { & $py[0] $py[1] -m venv (Join-Path $Root ".venv") } else { & $py[0] -m venv (Join-Path $Root ".venv") }
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the Python environment." }
}
& $venvPy -m pip install --upgrade pip --quiet

$hasNvidia = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($hasNvidia) {
    $index = "https://download.pytorch.org/whl/cu128"
    Write-Host "   NVIDIA GPU found: installing PyTorch with CUDA"
} else {
    $index = "https://download.pytorch.org/whl/cpu"
    Warn "No NVIDIA GPU driver found (nvidia-smi). Installing the CPU version of PyTorch (slower)."
}
& $venvPy -m pip install "torch==$TorchVersion" "torchaudio==$TorchAudioVersion" --index-url $index
if ($LASTEXITCODE -ne 0) { Fail "PyTorch installation failed (see the messages above)." }
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "Installing the Python packages failed (see the messages above)." }
Ok "Python packages"

# ---------------------------------------------------------------- 3. models
Step "3/8 AI models (downloaded once)"
& $venvPy -m rs2014conv.download_models
if ($LASTEXITCODE -ne 0) { Warn "Some models could not be downloaded. You can run setup again later." } else { Ok "models" }

# ---------------------------------------------------------------- 4. Rocksmith2014.NET
Step "4/8 Rocksmith2014.NET (DLC Builder's library)"
$rsnet = Join-Path $Root "third_party\Rocksmith2014.NET"
if (-not (Test-Path (Join-Path $rsnet "src\Rocksmith2014.DLCProject"))) {
    if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $Root ".git"))) {
        git -C $Root submodule update --init --recursive
    }
    if (-not (Test-Path (Join-Path $rsnet "src\Rocksmith2014.DLCProject"))) {
        Write-Host "   Downloading the source code..."
        $zip = Join-Path $env:TEMP "rs2014net.zip"
        Invoke-WebRequest "https://codeload.github.com/iminashi/Rocksmith2014.NET/zip/$RsNetCommit" -OutFile $zip -UseBasicParsing
        $tmp = Join-Path $env:TEMP "rs2014net_extract"
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        Expand-Archive $zip -DestinationPath $tmp
        if (Test-Path $rsnet) { Remove-Item -Recurse -Force $rsnet }
        New-Item -ItemType Directory -Force (Split-Path $rsnet) | Out-Null
        Move-Item (Join-Path $tmp "Rocksmith2014.NET-$RsNetCommit") $rsnet
        Remove-Item $zip
    }
}
Ok "Rocksmith2014.NET"

# ---------------------------------------------------------------- 5. .NET SDK + packager
Step "5/8 .NET 10 SDK and the packager"
Refresh-Path
$dotnet = $null
if (Get-Command dotnet -ErrorAction SilentlyContinue) {
    $sdks = & dotnet --list-sdks 2>$null
    if ($sdks -match "^10\.") { $dotnet = "dotnet" }
}
$localDotnet = Join-Path $Root "runtime\dotnet\dotnet.exe"
if (-not $dotnet -and (Test-Path $localDotnet)) { $dotnet = $localDotnet }
if (-not $dotnet) {
    # Into this folder (no admin rights, nothing changed on the system).
    Write-Host "   Installing the .NET 10 SDK into runtime\dotnet..."
    $installer = Join-Path $env:TEMP "dotnet-install.ps1"
    Invoke-WebRequest "https://dot.net/v1/dotnet-install.ps1" -OutFile $installer -UseBasicParsing
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Channel 10.0 -InstallDir (Join-Path $Root "runtime\dotnet") -NoPath
    Remove-Item $installer -ErrorAction SilentlyContinue
    if (-not (Test-Path $localDotnet)) { Fail "The .NET 10 SDK could not be installed (see the messages above)." }
    $dotnet = $localDotnet
}
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"
$packagerExe = Join-Path $Root "packager\publish\RsPackager.exe"
# Only (re)build when the packager's sources are newer than the built one (updates stay quick).
$sources = Get-ChildItem (Join-Path $Root "packager") -File | Where-Object { $_.Extension -in ".fs", ".fsproj", ".props", ".config" }
$upToDate = (Test-Path $packagerExe) -and -not ($sources | Where-Object { $_.LastWriteTime -gt (Get-Item $packagerExe).LastWriteTime })
if (-not $upToDate) {
    & $dotnet publish (Join-Path $Root "packager\RsPackager.fsproj") -c Release -r win-x64 --self-contained true -o (Join-Path $Root "packager\publish")
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path $packagerExe) { Warn "Rebuilding the packager failed; the existing one is kept." }
        else { Fail "Building the packager failed (see the messages above)." }
    }
}
Ok "packager\publish\RsPackager.exe"

# ---------------------------------------------------------------- 6. Wwise
Step "6/8 Wwise (needed to convert the audio for Rocksmith)"
& $venvPy -c "from rs2014conv.desktop import find_wwise; p = find_wwise(); print('   ' + str(p) if p else ''); raise SystemExit(0 if p else 3)"
if ($LASTEXITCODE -eq 0) {
    Ok "Wwise found"
} else {
    Warn "Wwise 2023 is not installed yet. It is free, but needs an Audiokinetic account, so you install it yourself (once):"
    Write-Host "     1. On the page that opens, create a free account and download the Audiokinetic Launcher."
    Write-Host "     2. In the Launcher: Wwise tab -> Install a new version -> the newest 2023.1.x (not 2024/2025)."
    Write-Host "     3. Tick only 'Authoring' under Packages, then Install."
    Write-Host "     4. Start Wwise once (Launcher -> Wwise 2023 -> Launch) and close it again."
    Write-Host "   You don't need to run this setup again afterwards: the CDLC Maker finds Wwise by itself."
    Write-Host "   Until then everything works except the final .psarc."
    if (-not $Unattended) { Start-Process "https://www.audiokinetic.com/download/" }
}

# ---------------------------------------------------------------- 7. Bass Trainer link
# Registers native_host\bass_trainer_host.bat with Chrome (and Edge / Brave) as a "native
# messaging host", so the Bass Trainer extension's player can hand a song to this tool.
# Only that extension (fixed ID) may start it. Per user (HKCU), no admin rights needed.
Step "7/8 Connect to the Bass Trainer browser extension"
$hostName = "com.basstrainer.rs2014converter"
$extensionId = "gbbniikkcomblgdcdmbcanlcilmaeohh"
$hostBat = Join-Path $Root "native_host\bass_trainer_host.bat"
$hostJson = Join-Path $Root "native_host\$hostName.json"
$manifest = [ordered]@{
    name = $hostName
    description = "RS2014converter for the Bass Trainer extension"
    path = $hostBat
    type = "stdio"
    allowed_origins = @("chrome-extension://$extensionId/")
}
# UTF-8 without BOM (a user folder may have non-ASCII letters).
[IO.File]::WriteAllText($hostJson, ($manifest | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
foreach ($browserKey in @("Software\Google\Chrome", "Software\Microsoft\Edge", "Software\BraveSoftware\Brave-Browser")) {
    $key = "HKCU:\$browserKey\NativeMessagingHosts\$hostName"
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -Path $key -Name "(default)" -Value $hostJson
}
Ok "Bass Trainer can now build CDLC with one click (reload the Bass Trainer extension / its player page)."

# ---------------------------------------------------------------- 8. Rocksmith folder + shortcuts
Step "8/8 Rocksmith dlc folder and shortcuts"
& $venvPy -m rs2014conv.desktop setup
New-Item -ItemType Directory -Force (Join-Path $Root "songs") | Out-Null
$pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$app = Join-Path $Root "app\cdlc_maker.pyw"
$icon = Join-Path $Root "app\icon.ico"
$shell = New-Object -ComObject WScript.Shell
function Make-Shortcut($path, $target, $arguments, $description) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $target
    $s.Arguments = $arguments
    $s.WorkingDirectory = $Root
    $s.Description = $description
    if (Test-Path $icon) { $s.IconLocation = $icon }
    $s.Save()
}
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "RS2014converter"
New-Item -ItemType Directory -Force $menu | Out-Null
$desktop = [Environment]::GetFolderPath("Desktop")
Make-Shortcut (Join-Path $desktop "RS2014 CDLC Maker.lnk") $pythonw "`"$app`"" "Make Rocksmith 2014 bass CDLC (drop an audio file or song folder on it)"
Make-Shortcut (Join-Path $menu "RS2014 CDLC Maker.lnk") $pythonw "`"$app`"" "Make Rocksmith 2014 bass CDLC"
Make-Shortcut (Join-Path $menu "Update RS2014converter.lnk") "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $Root 'install.ps1')`" -Update" "Get the newest version"
Make-Shortcut (Join-Path $menu "Songs folder.lnk") (Join-Path $Root "songs") "" "Songs made with the CDLC Maker and the Bass Trainer extension"
Ok "Desktop and Start menu: 'RS2014 CDLC Maker'"

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
