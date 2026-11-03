# RS2014converter installer and updater. No admin rights needed.
#
# Install (Win+R, paste, Enter):
#   powershell -NoExit -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/dom1911k/RS2014converter/refs/heads/claude/elegant-brown-1ku0ek/install.ps1 | iex"
#
# It downloads the newest version into %LOCALAPPDATA%\RS2014converter (or updates the copy it is
# started from), runs setup.ps1 (Python, AI models, packager, Rocksmith dlc folder, shortcuts,
# Bass Trainer link) and opens the RS2014 CDLC Maker. Run it again (or the Start menu shortcut
# "Update RS2014converter") to update: your settings, songs, cache and the installed parts stay.

param([switch]$Update, [string]$Root = "", [switch]$NoSetup, [switch]$NoLaunch)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Owner = "dom1911k"; $Repo = "RS2014converter"; $Branch = "claude/elegant-brown-1ku0ek"
# Kept when updating: your settings and data, and what setup installed or built.
$Keep = @(".venv", "cache", "songs", "output", "runtime", "settings.yaml", "version.json", "upload",
          "packager\publish", "packager\bin", "packager\obj", "third_party\Rocksmith2014.NET",
          "native_host\com.basstrainer.rs2014converter.json", "app\icon.ico")

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

# Everything in a function: "return" ends it without closing the window (with "irm | iex").
function Install-RS2014converter {
$temp = [IO.Path]::GetTempPath()

if (-not $Root) {
    $here = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { "" }
    $Root = if ($here -and (Test-Path (Join-Path $here "make_cdlc.py"))) { $here } else { Join-Path $env:LOCALAPPDATA "RS2014converter" }
}
$fresh = -not (Test-Path (Join-Path $Root "make_cdlc.py"))
Say ""
Say "RS2014converter $(if ($fresh) { 'installation' } else { 'update' }) -> $Root" "Cyan"

# ---------------------------------------------------------------- newest version
$latest = $null
try {
    $info = Invoke-RestMethod "https://api.github.com/repos/$Owner/$Repo/branches/$([uri]::EscapeDataString($Branch))" -UseBasicParsing -Headers @{ "User-Agent" = "RS2014converter-installer" }
    $latest = @{ sha = $info.commit.sha; date = $info.commit.commit.committer.date; message = ($info.commit.commit.message -split "`n")[0] }
} catch { Say "   (Could not ask GitHub for the newest version: $($_.Exception.Message))" "Yellow" }
$installed = $null
$versionFile = Join-Path $Root "version.json"
if (Test-Path $versionFile) { try { $installed = Get-Content $versionFile -Raw | ConvertFrom-Json } catch { } }
$isGit = Test-Path (Join-Path $Root ".git")

if ($isGit) {
    Say "   This copy is a git checkout: updating with git pull." "Gray"
    git -C $Root pull --ff-only
} elseif ($fresh -or -not $installed -or -not $latest -or $installed.sha -ne $latest.sha) {
    # ------------------------------------------------------------ download and unpack
    if ($latest -and $installed -and $installed.sha -eq $latest.sha) { Say "   Already the newest version." "Green" }
    $ref = if ($latest) { $latest.sha } else { "refs/heads/$Branch" }
    $zip = Join-Path $temp "rs2014converter.zip"
    $tmp = Join-Path $temp "rs2014converter_unpack"
    Say "   Downloading..."
    Invoke-WebRequest "https://codeload.github.com/$Owner/$Repo/zip/$ref" -OutFile $zip -UseBasicParsing
    if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
    Expand-Archive $zip -DestinationPath $tmp
    $src = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName
    New-Item -ItemType Directory -Force $Root | Out-Null
    # Settings you changed in config.yaml before settings.yaml existed: carry them over.
    $oldConfig = Join-Path $Root "config.yaml"
    if ((Test-Path $oldConfig) -and -not (Test-Path (Join-Path $Root "settings.yaml"))) {
        $m = Select-String -Path $oldConfig -Pattern "^\s*copy_to_folder:\s*'?""?([^'""#]+)" | Select-Object -First 1
        if ($m -and $m.Matches[0].Groups[1].Value.Trim()) {
            $dest = $m.Matches[0].Groups[1].Value.Trim()
            Set-Content (Join-Path $Root "settings.yaml") "# Your settings (kept when the tool is updated). Defaults: config.yaml`npackaging:`n  copy_to_folder: '$dest'" -Encoding UTF8
        }
    }
    # Copy the new files over the old ones, except what is kept.
    $keepFull = $Keep | ForEach-Object { (Join-Path $Root $_).TrimEnd('\', '/') }
    Get-ChildItem $src -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($src.Length).TrimStart('\', '/')
        $dst = Join-Path $Root $rel
        $kept = $false
        foreach ($k in $keepFull) { if ($dst -eq $k -or $dst.StartsWith($k + [IO.Path]::DirectorySeparatorChar)) { $kept = $true; break } }
        if (-not $kept) {
            New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
            Copy-Item $_.FullName $dst -Force
        }
    }
    Remove-Item -Recurse -Force $tmp, $zip -ErrorAction SilentlyContinue
    if ($latest) {
        ($latest | ConvertTo-Json) | Set-Content $versionFile -Encoding UTF8
        Say "   Version $($latest.sha.Substring(0, 7)) ($($latest.date)): $($latest.message)" "Green"
    }
} else {
    Say "   Already the newest version ($($installed.sha.Substring(0, 7)))." "Green"
}

# ---------------------------------------------------------------- setup
if (-not $NoSetup) {
    $setupArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Root "setup.ps1"))
    if ($Update) { $setupArgs += "-Unattended" }   # no browser tabs opening during an update
    & powershell @setupArgs
    if ($LASTEXITCODE -ne 0) {
        Say ""
        Say "Setup stopped (see above). Fix the problem and run the installer again: it continues where it stopped." "Red"
        return
    }
}

$pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
if (-not $NoLaunch -and (Test-Path $pythonw)) {
    Say ""
    Say "Starting the RS2014 CDLC Maker (also on your desktop and in the Start menu)." "Green"
    Start-Process $pythonw -ArgumentList "`"$(Join-Path $Root 'app\cdlc_maker.pyw')`"" -WorkingDirectory $Root
}
Say ""
Say "Done. You can close this window." "Green"
}

try {
    Install-RS2014converter
} catch {
    Say ""
    Say "Installation stopped: $($_.Exception.Message)" "Red"
    Say "Check your internet connection and run the same command again: it continues where it stopped." "Yellow"
}
