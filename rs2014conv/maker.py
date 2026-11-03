"""The logic behind the RS2014 CDLC Maker app (app/cdlc_maker.pyw); no GUI code here.

Every song lives in a folder of the songs folder (the same place the Bass Trainer extension
sends songs to): song.yaml, and the audio / tab either in the folder or referenced by path.
Building runs make_cdlc.py on that folder; results go to <folder>/output/<Artist - Title>/.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config

AUDIO_EXT = {".flac", ".wav", ".mp3", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".aiff", ".aif"}
TAB_EXT = {".gp3", ".gp4", ".gp5", ".gpx", ".gp", ".txt", ".tab"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
REPO = "dom1911k/RS2014converter"
BRANCH = "claude/elegant-brown-1ku0ek"


def songs_dir(cfg: dict | None = None) -> Path:
    cfg = cfg if cfg is not None else config.load(None)
    custom = ((cfg.get("bass_trainer") or {}).get("songs_folder") or "").strip()
    return Path(custom) if custom else config.ROOT / "songs"


def safe_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
    return name[:100] or "Song"


@dataclass
class Song:
    title: str = ""
    artist: str = ""
    album: str = ""
    year: int = 0
    audio: str = ""  # path (absolute, or relative to the folder)
    tab: str = ""
    tuning: str = ""  # "" = automatic
    folder: Path | None = None
    extra: dict = field(default_factory=dict)  # other song.yaml keys (chart_offset, strings...)

    def path(self, value: str) -> Path | None:
        if not value:
            return None
        p = Path(value)
        return p if p.is_absolute() or self.folder is None else self.folder / p

    def missing(self) -> list[str]:
        out = []
        if not self.title.strip():
            out.append("a title")
        if not self.artist.strip():
            out.append("the artist")
        a = self.path(self.audio)
        if a is None or not a.exists():
            out.append("the audio file")
        t = self.path(self.tab)
        if self.tab and (t is None or not t.exists()):
            out.append("the tab file (it was moved?)")
        return out


def _first(folder: Path, exts: set[str], exclude: tuple[str, ...] = ()) -> str:
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts
                   and not any(x in p.stem.lower() for x in exclude))
    return files[0].name if files else ""


def guess_from_name(path: Path) -> tuple[str, str]:
    """'Artist - Title.flac' -> (artist, title); otherwise ('', stem)."""
    stem = re.sub(r"\s*\((?:bass chart|RS2014 kit|official.*?|audio)\)\s*$", "", path.stem.replace(".btchart", ""), flags=re.I)
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip(), t.strip()
    return "", stem.strip()


def load_song(path: Path) -> Song:
    """A song from a song folder, an audio file or a tab file."""
    path = Path(path)
    if path.is_dir():
        data = config.load_song_yaml(path)
        s = Song(folder=path, title=str(data.pop("title", "") or ""), artist=str(data.pop("artist", "") or ""),
                 album=str(data.pop("album", "") or ""), year=int(data.pop("year", 0) or 0),
                 audio=str(data.pop("audio", "") or ""), tab=str(data.pop("tab", "") or ""),
                 tuning=str(data.pop("tuning", "") or ""), extra=data)
        if not s.audio:
            s.audio = _first(path, AUDIO_EXT, exclude=("bass", "stem", "preview"))
        if not s.tab:
            from .tabs import is_btchart

            s.tab = _first(path, TAB_EXT - {".txt"}) or next((p.name for p in sorted(path.glob("*.json")) if is_btchart(p)), "")
        if not s.title:
            s.artist, s.title = s.artist or guess_from_name(path)[0], guess_from_name(path)[1]
        return s
    artist, title = guess_from_name(path)
    if path.suffix.lower() in AUDIO_EXT:
        return Song(title=title, artist=artist, audio=str(path))
    s = Song(title=title, artist=artist, tab=str(path))
    try:
        from .tabs import read_tab

        t = read_tab(path)
        s.title, s.artist = t.title or s.title, t.artist or s.artist
    except Exception:  # noqa: BLE001 - an unreadable tab just gives no title
        pass
    return s


def save_song(song: Song, base: Path | None = None) -> Path:
    """Write song.yaml (into the song's folder, or a new folder in the songs folder)."""
    if song.folder is None:
        song.folder = (base or songs_dir()) / safe_name(f"{song.artist} - {song.title}")
    song.folder.mkdir(parents=True, exist_ok=True)

    def rel(v: str) -> str:
        p = Path(v)
        try:
            return str(p.relative_to(song.folder)) if p.is_absolute() else v
        except ValueError:
            return v
    data = {"title": song.title.strip(), "artist": song.artist.strip()}
    if song.album.strip():
        data["album"] = song.album.strip()
    if song.year:
        data["year"] = int(song.year)
    if song.audio:
        data["audio"] = rel(song.audio)
    if song.tab:
        data["tab"] = rel(song.tab)
    if song.tuning.strip():
        data["tuning"] = song.tuning.strip()
    data.update(song.extra)
    (song.folder / "song.yaml").write_text("# Made with the RS2014 CDLC Maker\n" + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                                           encoding="utf-8")
    return song.folder


def build_command(folder: Path) -> list[str]:
    return [sys.executable, str(config.ROOT / "make_cdlc.py"), str(folder)]


@dataclass
class Result:
    out_dir: Path | None
    psarc: Path | None
    checks: list[str]
    check_mix: Path | None


def report_checks(report: Path) -> list[str]:
    """The 'PLEASE CHECK' items of report.txt."""
    if not report.exists():
        return []
    out, inside = [], False
    for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("PLEASE CHECK"):
            inside = True
            continue
        if inside:
            if line.startswith("  * "):
                out.append(line[4:])
            elif not line.strip():
                break
    return out


def latest_result(folder: Path) -> Result | None:
    out_root = Path(folder) / "output"
    reports = sorted(out_root.glob("*/report.txt"), key=lambda p: p.stat().st_mtime) if out_root.exists() else []
    if not reports:
        return None
    out = reports[-1].parent
    psarcs = sorted(out.glob("*.psarc"), key=lambda p: p.stat().st_mtime)
    mixes = sorted((out / "review").glob("check_mix*.wav")) if (out / "review").exists() else []
    return Result(out, psarcs[-1] if psarcs else None, report_checks(reports[-1]), mixes[0] if mixes else None)


def list_songs(base: Path | None = None) -> list[tuple[Path, Song, Result | None]]:
    """The songs in the songs folder, most recently changed first."""
    base = base or songs_dir()
    if not base.exists():
        return []
    out = []
    for d in base.iterdir():
        if d.is_dir() and (d / "song.yaml").exists():
            try:
                out.append((d, load_song(d), latest_result(d)))
            except Exception:  # noqa: BLE001 - a broken song.yaml shouldn't hide the others
                continue
    out.sort(key=lambda x: max([(x[0] / "song.yaml").stat().st_mtime] + ([x[2].out_dir.stat().st_mtime] if x[2] else [])), reverse=True)
    return out


def installed_version() -> str:
    try:
        return json.loads((config.ROOT / "version.json").read_text(encoding="utf-8-sig"))["sha"]
    except Exception:  # noqa: BLE001
        return ""


def newest_version(timeout: float = 6.0) -> tuple[str, str] | None:
    """(sha, message) of the newest version on GitHub, or None (offline)."""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/branches/{BRANCH}",
                                 headers={"User-Agent": "RS2014converter-cdlc-maker"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = json.load(r)
        return info["commit"]["sha"], info["commit"]["commit"]["message"].splitlines()[0]
    except Exception:  # noqa: BLE001
        return None
