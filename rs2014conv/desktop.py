"""Desktop integration for Windows: find Rocksmith's dlc folder and Wwise, make the app icon.

Used by setup.ps1 (`python -m rs2014conv.desktop setup`) and by the CDLC Maker app.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from .config import ROOT

WWISE_DOWNLOAD = "https://www.audiokinetic.com/download/"


def steam_libraries(steam_root: Path) -> list[Path]:
    """Steam library folders listed in steamapps/libraryfolders.vdf (plus Steam's own folder)."""
    libs = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
            p = Path(m.group(1).replace("\\\\", "\\"))
            if p not in libs:
                libs.append(p)
    return libs


def _steam_roots() -> list[Path]:
    roots = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, key, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(Path(winreg.QueryValueEx(k, name)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if pf:
                roots.append(Path(pf) / "Steam")
    return roots


def find_rocksmith_dlc(steam_roots: list[Path] | None = None) -> Path | None:
    """Rocksmith 2014's dlc folder in any Steam library, or None."""
    seen = set()
    for root in steam_roots if steam_roots is not None else _steam_roots():
        for lib in steam_libraries(Path(root)):
            dlc = lib / "steamapps" / "common" / "Rocksmith2014" / "dlc"
            key = str(dlc).lower()
            if key in seen:
                continue
            seen.add(key)
            if dlc.is_dir():
                return dlc
    return None


def _registry_env(name: str) -> list[str]:
    """An environment variable as stored in Windows (user, then system), not as this process
    inherited it: a program started before Wwise was installed (e.g. Chrome, and so the Bass
    Trainer link) doesn't have WWISEROOT yet."""
    if sys.platform != "win32":
        return []
    import winreg

    out = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Environment"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as k:
                out.append(os.path.expandvars(str(winreg.QueryValueEx(k, name)[0])))
        except OSError:
            pass
    return out


def find_wwise(cfg: dict | None = None) -> Path | None:
    """WwiseConsole.exe of an installed Wwise 2019-2023, or None."""
    given = ((cfg or {}).get("packaging") or {}).get("wwise_console")
    if given and Path(given).exists():
        return Path(given)
    bases = []
    for root in ([os.environ["WWISEROOT"]] if os.environ.get("WWISEROOT") else []) + _registry_env("WWISEROOT"):
        if root and Path(root) not in bases:
            bases.append(Path(root))
    for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if pf and (Path(pf) / "Audiokinetic").is_dir():
            bases += sorted((d for d in (Path(pf) / "Audiokinetic").iterdir()
                             if re.search(r"2019|2021|2022|2023", d.name)), reverse=True)
    for b in bases:
        exe = b / "Authoring" / "x64" / "Release" / "bin" / "WwiseConsole.exe"
        if exe.exists():
            return exe
    return None


BASS_TRAINER_ID = "gbbniikkcomblgdcdmbcanlcilmaeohh"   # fixed by the key in the extension's manifest


def browsers() -> list[Path]:
    """Chrome, Edge and Brave (the browsers the Bass Trainer link is registered for)."""
    if sys.platform != "win32":
        return []
    import winreg

    out = []
    for exe in ("chrome.exe", "msedge.exe", "brave.exe"):
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                    p = Path(winreg.QueryValueEx(k, "")[0])
                if p.exists() and p not in out:
                    out.append(p)
            except OSError:
                pass
    return out


def bass_trainer_test_url(song_folder: Path) -> str:
    from urllib.parse import quote

    return f"chrome-extension://{BASS_TRAINER_ID}/player/player.html#test={quote(str(Path(song_folder).resolve()), safe='')}"


def open_in_bass_trainer(song_folder: Path) -> bool:
    """Open the Bass Trainer player on a song's finished CDLC chart. False if no browser."""
    import subprocess

    for exe in browsers():
        try:
            subprocess.Popen([str(exe), bass_trainer_test_url(song_folder)])
            return True
        except OSError:
            continue
    return False


def make_icon(path: Path) -> Path:
    """A simple app icon (a yellow note gem on dark), as .ico."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), radius=56, fill=(21, 23, 28, 255))
    for i, (y, col) in enumerate([(70, (232, 32, 42)), (112, (242, 196, 24)), (154, (60, 140, 255)), (196, (255, 140, 40))]):
        d.rectangle((36, y - 4, 220, y + 4), fill=col)
    d.rounded_rectangle((92, 92, 188, 132), radius=12, fill=(242, 196, 24, 255), outline=(255, 255, 255), width=5)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def setup() -> int:
    """After installing: remember the Rocksmith dlc folder, make the icon, report Wwise."""
    from . import config

    cfg = config.load(None)
    dest = (cfg.get("packaging") or {}).get("copy_to_folder") or ""
    if dest:
        print(f"   Rocksmith dlc folder: {dest}")
        if not (config.load_user_settings().get("packaging") or {}).get("copy_to_folder"):
            config.save_user_setting("packaging", "copy_to_folder", dest)  # moved out of config.yaml
    else:
        dlc = find_rocksmith_dlc()
        if dlc:
            config.save_user_setting("packaging", "copy_to_folder", str(dlc))
            print(f"   Found Rocksmith: finished CDLC go straight into {dlc}")
        else:
            print("   Rocksmith 2014 not found in Steam: finished CDLC stay in each song's output folder.")
            print("   (You can choose the dlc folder later in the CDLC Maker app.)")
    wwise = find_wwise(cfg)
    if wwise and not (config.load_user_settings().get("packaging") or {}).get("wwise_console"):
        config.save_user_setting("packaging", "wwise_console", str(wwise))
    try:
        make_icon(ROOT / "app" / "icon.ico")
    except Exception as e:  # noqa: BLE001 - the icon is optional
        print(f"   (no icon: {e})")
    return 0 if wwise else 3   # 3 = Wwise missing


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        sys.exit(setup())
    print("usage: python -m rs2014conv.desktop setup")
