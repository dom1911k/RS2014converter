# make_cdlc one-time setup for Windows 10/11. Started by setup.bat; safe to run again.
#
# Installs (only what is missing):
#   1. Python 3.12            (winget)
#   2. A private Python environment in .venv with PyTorch (CUDA) + the audio libraries
#   3. The AI models (downloaded once, then everything works offline)
#   4. Rocksmith2014.NET source (the library behind DLC Builder)
#   5. The .NET 10 SDK        (winget) and builds the packager (packager\publish\RsPackager.exe)
# and checks for Wwise, which you install yourself (see README).

# "Continue": native tools (pip, py, dotnet) write progress to stderr, which must not abort the
# script in Windows PowerShell 5.1. Every step checks $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$RsNetCommit = "4cf3325174e13f1dc5ce997e7663b2851c6afcce"
$TorchVersion = "2.11.0"
$TorchAudioVersion = "2.11.0"

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

# ---------------------------------------------------------------- 1. Python
Step "1/6 Python 3.12"
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $py = @("py", "-3.12") }
}
if (-not $py) {
    if (-not (Has-Winget)) { Fail "Python 3.12 not found and winget is missing. Install Python 3.12 from python.org, then run setup.bat again." }
    Write-Host "   Installing Python 3.12 with winget..."
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Refresh-Path
    & py -3.12 -c "import sys" 2>$null
    if ($LASTEXITCODE -ne 0) { Fail "Python was installed but is not found yet. Close this window and run setup.bat again." }
    $py = @("py", "-3.12")
}
Ok "Python 3.12"

# ---------------------------------------------------------------- 2. venv + packages
Step "2/6 Python packages (this downloads a few GB the first time)"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $py[0] $py[1] -m venv (Join-Path $Root ".venv")
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
Step "3/6 AI models (downloaded once)"
& $venvPy -m rs2014conv.download_models
if ($LASTEXITCODE -ne 0) { Warn "Some models could not be downloaded. You can run setup.bat again later." } else { Ok "models" }

# ---------------------------------------------------------------- 4. Rocksmith2014.NET
Step "4/6 Rocksmith2014.NET (DLC Builder's library)"
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
Step "5/6 .NET 10 SDK and the packager"
Refresh-Path
$hasSdk = $false
if (Get-Command dotnet -ErrorAction SilentlyContinue) {
    $sdks = & dotnet --list-sdks 2>$null
    if ($sdks -match "^10\.") { $hasSdk = $true }
}
if (-not $hasSdk) {
    if (-not (Has-Winget)) { Fail ".NET 10 SDK missing and winget not available. Install the .NET 10 SDK from https://dotnet.microsoft.com/download and run setup.bat again." }
    Write-Host "   Installing the .NET 10 SDK with winget..."
    winget install -e --id Microsoft.DotNet.SDK.10 --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
& dotnet publish (Join-Path $Root "packager\RsPackager.fsproj") -c Release -r win-x64 --self-contained false -o (Join-Path $Root "packager\publish")
if ($LASTEXITCODE -ne 0) { Fail "Building the packager failed (see the messages above)." }
Ok "packager\publish\RsPackager.exe"

# ---------------------------------------------------------------- 6. Wwise
Step "6/6 Wwise (needed to convert the audio for Rocksmith)"
$wwise = @()
foreach ($pf in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
    if ($pf -and (Test-Path (Join-Path $pf "Audiokinetic"))) {
        $wwise += Get-ChildItem (Join-Path $pf "Audiokinetic") -Directory | Where-Object { $_.Name -match "2019|2021|2022|2023" }
    }
}
if ($env:WWISEROOT) { Write-Host "   WWISEROOT = $env:WWISEROOT" }
if ($wwise.Count -gt 0) {
    Ok ("found " + (($wwise | ForEach-Object { $_.Name }) -join ", "))
} else {
    Warn "Wwise 2023 was not found. Install it with the Audiokinetic Launcher (see README, section 'Wwise')."
    Warn "Without Wwise, make_cdlc still makes everything except the final .psarc."
}

$dlc = Join-Path ${env:ProgramFiles(x86)} "Steam\steamapps\common\Rocksmith2014\dlc"
if (Test-Path $dlc) {
    Write-Host ""
    Write-Host "   Your Rocksmith dlc folder: $dlc"
    Write-Host "   Tip: set packaging -> copy_to_folder in config.yaml to this path to install CDLC automatically."
}

Write-Host ""
Write-Host "Setup finished." -ForegroundColor Green
