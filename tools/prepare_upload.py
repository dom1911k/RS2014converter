"""Make small upload packages of songs for testing (audio compressed to MP3).

  python tools/prepare_upload.py "C:\\Songs\\My Song" ["C:\\Songs\\Other Song" ...]   (song folders)
  python tools/prepare_upload.py song.flac --tab song.gp5                            (single file)

Each song becomes upload\\<song>.zip with the audio as a 192 kbps MP3 (about 1.5 MB per minute),
plus the tab and song.yaml. If a zip would still be over the limit (--max-mb, default 28), the
bitrate is lowered. 192 kbps keeps everything the analysis uses.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

AUDIO_EXT = {".flac", ".wav", ".mp3", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".aiff", ".aif"}
TAB_EXT = {".gp3", ".gp4", ".gp5", ".gpx", ".gp", ".txt", ".tab"}


def pick_audio(folder: Path) -> Path | None:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT
                   and not any(x in p.stem.lower() for x in ("bass", "stem", "preview")))
    return files[0] if files else None


def encode(src: Path, dst: Path, kbps: int) -> None:
    from rs2014conv.audio_io import ffmpeg_exe

    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(src), "-map", "0:a:0", "-c:a", "libmp3lame",
                    "-b:a", f"{kbps}k", str(dst)], check=True)


def package(name: str, audio: Path, extras: list[Path], out: Path, max_mb: float) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    mp3 = out / f"{name}.mp3"
    zpath = out / f"{name}.zip"
    for kbps in (192, 160, 128, 96):
        encode(audio, mp3, kbps)
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(mp3, f"{name}/{audio.stem}.mp3")
            for f in extras:
                z.write(f, f"{name}/{f.name}")
        if zpath.stat().st_size <= max_mb * 1e6:
            break
    mp3.unlink(missing_ok=True)
    print(f"{zpath.name}: {zpath.stat().st_size / 1e6:.1f} MB ({kbps} kbps)")
    return zpath


def main() -> int:
    ap = argparse.ArgumentParser(description="Small test packages: audio as MP3 + tab + song.yaml")
    ap.add_argument("inputs", nargs="+", help="song folders, or one audio file")
    ap.add_argument("--tab", help="tab for a single audio file")
    ap.add_argument("--out", default=str(ROOT / "upload"))
    ap.add_argument("--max-mb", type=float, default=28.0)
    args = ap.parse_args()
    out = Path(args.out)
    for item in map(Path, args.inputs):
        if item.is_dir():
            audio = pick_audio(item)
            if audio is None:
                print(f"{item.name}: no audio file found, skipped")
                continue
            extras = [p for p in item.iterdir() if p.suffix.lower() in TAB_EXT or p.name in ("song.yaml", "song.yml")]
            package(item.name, audio, extras, out, args.max_mb)
        elif item.suffix.lower() in AUDIO_EXT:
            extras = [Path(args.tab)] if args.tab else []
            package(item.stem, item, extras, out, args.max_mb)
        else:
            print(f"{item}: not a song folder or audio file, skipped")
    print(f"\nUpload the zip files from: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
