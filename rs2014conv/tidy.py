"""Keep the output small. A finished song needs only its .psarc; the rest is for checking or
rebuilding it, so that is kept in small (compressed) files:

* review\\check_mix.mp3 / check_bass_vs_chart.mp3 instead of WAVs (75 MB each for 7 minutes);
* after a successful build, the project's audio as audio.ogg (a few MB) instead of the WAV/FLAC
  and the converted .wem files: "Build the .psarc again" still works, Wwise converts it again;
* the EOF project (eof\\, for hand edits in Editor on Fire) only with output.keep_everything.

Also clears old entries of the cache (bass stems etc.) and can tidy every song at once:

    python -m rs2014conv.tidy            (all songs + the cache; prints how much was freed)
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

BIG_AUDIO = {".wav", ".flac"}


def size_of(path: Path) -> int:
    path = Path(path)
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) if path.exists() else 0


def save_listening(path: Path, audio: np.ndarray, sr: int, fmt: str = "mp3") -> Path:
    """A file to listen to: MP3 (plays everywhere, ~1 MB per minute) or WAV. `path`'s
    extension is replaced; returns the file written (WAV if MP3 encoding isn't available)."""
    audio = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    if audio.ndim == 1:
        audio = audio[None]
    path = Path(path)
    if str(fmt).lower() == "mp3":
        out = path.with_suffix(".mp3")
        try:
            sf.write(str(out), audio.T, sr, format="MP3", subtype="MPEG_LAYER_III")
            for old in (path.with_suffix(".wav"),):
                old.unlink(missing_ok=True)
            return out
        except Exception:  # noqa: BLE001 - an old libsndfile without MP3: fall back to WAV
            out.unlink(missing_ok=True)
    out = path.with_suffix(".wav")
    sf.write(str(out), audio.T, sr, subtype="PCM_16")
    path.with_suffix(".mp3").unlink(missing_ok=True)
    return out


def _wav_to_mp3(wav: Path) -> int:
    before = wav.stat().st_size
    try:
        data, sr = sf.read(str(wav), dtype="float32", always_2d=True)
        out = save_listening(wav, data.T, sr)
    except Exception:  # noqa: BLE001 - leave the file as it is
        return 0
    return before - out.stat().st_size if out != wav else 0


def _built(out_dir: Path) -> bool:
    return any(out_dir.glob("*.psarc"))


def slim_project(proj_dir: Path) -> int:
    """After a successful build: the project's audio as audio.ogg, without the .wem files
    and the preview (both made again when the .psarc is built again). Returns bytes freed."""
    projects = sorted(proj_dir.glob("*.rs2dlc"))
    if not projects:
        return 0
    rs2dlc = projects[0]
    try:
        p = json.loads(rs2dlc.read_text(encoding="utf-8"))
        audio_rel = p["AudioFile"]["Path"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    audio = Path(audio_rel) if Path(audio_rel).is_absolute() else proj_dir / audio_rel
    if not audio.exists():
        return 0
    before = size_of(proj_dir)
    if audio.suffix.lower() in BIG_AUDIO:
        ogg = audio.with_suffix(".ogg")
        tmp = audio.with_name(audio.stem + ".tmp.ogg")
        try:
            from .audio_io import encode_ogg

            encode_ogg(audio, tmp, quality=5)
            tmp.replace(ogg)
        except Exception:  # noqa: BLE001 - no encoder: keep the big file, the CDLC is fine anyway
            tmp.unlink(missing_ok=True)
            return 0
        p["AudioFile"]["Path"] = str(Path(audio_rel).with_suffix(".ogg"))
        rs2dlc.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
        audio.unlink()
    # (already .ogg: built again from it - the new .wem files and preview can go as well)
    for f in list(proj_dir.glob("*.wem")) + list(proj_dir.glob("*_preview.wav")):
        f.unlink(missing_ok=True)
    shutil.rmtree(proj_dir / "eof", ignore_errors=True)
    return before - size_of(proj_dir)


def tidy_output(out_dir: Path, keep_everything: bool = False) -> int:
    """Make one song's output folder small. Returns the bytes freed."""
    out_dir = Path(out_dir)
    freed = 0
    review = out_dir / "review"
    if review.exists():
        for wav in sorted(review.glob("*.wav")):
            freed += _wav_to_mp3(wav)
    if not keep_everything and _built(out_dir) and (out_dir / "project").exists():
        freed += slim_project(out_dir / "project")
    return freed


def prune_cache(cache_dir: Path, max_age_days: float = 30, everything: bool = False) -> int:
    """Delete cache files (bass stems, beat maps, pitch tracks) not used for `max_age_days`
    (or all of them). They only make building the same song again faster."""
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    limit = time.time() - max_age_days * 86400
    freed = 0
    for f in cache_dir.iterdir():
        if f.is_file() and (everything or f.stat().st_mtime < limit or f.name.startswith("_")):
            try:
                n = f.stat().st_size
                f.unlink()
                freed += n
            except OSError:
                pass
    return freed


def tidy_all(songs_dir: Path, cache_dir: Path | None = None, keep_everything: bool = False) -> int:
    """Tidy the output of every song in the songs folder, and empty the cache."""
    freed = 0
    songs_dir = Path(songs_dir)
    if songs_dir.exists():
        for out in sorted(songs_dir.glob("*/output/*/")):
            if (out / "report.txt").exists() or (out / "project").exists():
                freed += tidy_output(out, keep_everything)
    if cache_dir is not None:
        freed += prune_cache(cache_dir, everything=True)
    return freed


def mb(n: int) -> str:
    return f"{n / 1e6:.0f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def main() -> int:
    from . import config, maker

    cfg = config.load(None)
    keep = bool((cfg.get("output") or {}).get("keep_everything"))
    freed = tidy_all(maker.songs_dir(cfg), Path(cfg["cache_dir"]), keep)
    print(f"Freed {mb(freed)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
