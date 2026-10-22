"""make_cdlc: turn a song (audio + optional bass tab) into a Rocksmith 2014 bass CDLC.

Two ways to use it:

  python make_cdlc.py song.flac --title "Song" --artist "Band" [--tab song.gp5] [--tuning "Drop D"]
  python make_cdlc.py path\\to\\song_folder        (folder with the audio, optional tab and a song.yaml)

See README.md for everything else.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

AUDIO_EXT = {".flac", ".wav", ".mp3", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".aiff", ".aif"}
TAB_EXT = {".gp3", ".gp4", ".gp5", ".gpx", ".gp", ".txt", ".tab"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Audio (+ bass tab) -> Rocksmith 2014 bass CDLC (.psarc)")
    ap.add_argument("input", help="audio file, or a song folder containing song.yaml")
    ap.add_argument("--title")
    ap.add_argument("--artist")
    ap.add_argument("--album")
    ap.add_argument("--year", type=int)
    ap.add_argument("--tab", help="Guitar Pro (.gp3/.gp4/.gp5/.gpx/.gp) or text tab")
    ap.add_argument("--track", type=int, help="track number in the tab file (1 = first); default: auto-detect bass")
    ap.add_argument("--stem", help="isolated bass stem (skips separation)")
    ap.add_argument("--tuning", help='e.g. "E Standard", "Drop D", "D Standard", "B E A D G"')
    ap.add_argument("--strings", type=int, choices=(4, 5, 6), help="strings on the bass (for tuning guesses)")
    ap.add_argument("--cover", help="album art image")
    ap.add_argument("--grid", help="quantization grid when transcribing: 1/8, 1/16, 1/8T, 1/16T, 1/16+T")
    ap.add_argument("--picked", action="store_true", help="mark the arrangement as played with a pick")
    ap.add_argument("--out", help="output folder (default: 'output' next to the audio)")
    ap.add_argument("--config", help="use another config file instead of config.yaml")
    ap.add_argument("--no-package", action="store_true", help="skip building the .psarc")
    args = ap.parse_args(argv)

    from rs2014conv import config as config_mod
    from rs2014conv.pipeline import SongInputs, run

    cfg = config_mod.load(Path(args.config) if args.config else None)
    src = Path(args.input).expanduser().resolve()
    song: dict = {}
    folder = None
    if src.is_dir():
        folder = src
        song = config_mod.load_song_yaml(folder)
        for k in list(song):
            if k in config_mod.DEFAULTS:  # per-song overrides of global settings
                cfg = config_mod._merge(cfg, {k: song.pop(k)})

        def find(key: str, exts: set[str], exclude: tuple[str, ...] = ()) -> Path | None:
            if song.get(key):
                return (folder / song[key]).resolve()
            files = sorted(p for p in folder.iterdir() if p.suffix.lower() in exts
                           and not any(x in p.stem.lower() for x in exclude))
            return files[0] if files else None

        audio = find("audio", AUDIO_EXT, exclude=("bass", "stem", "preview"))
        if audio is None:
            print(f"No audio file found in {folder}", file=sys.stderr)
            return 2
        tab = find("tab", TAB_EXT - {".txt"} if not song.get("tab") else TAB_EXT)
        if song.get("stem"):
            stem = (folder / song["stem"]).resolve()
        else:  # an audio file with "bass" or "stem" in its name is used as the bass stem
            stems = sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_EXT
                           and any(x in p.stem.lower() for x in ("bass", "stem")))
            stem = stems[0] if stems else None
        cover = find("cover", IMG_EXT)
    else:
        audio, tab, stem, cover = src, None, None, None

    def pick(name, default=None):
        v = getattr(args, name, None)
        return v if v not in (None, False) else song.get(name, default)

    title = pick("title") or (folder.name if folder else audio.stem)
    artist = pick("artist")
    if not artist:
        print("Please give the artist: --artist \"...\" (or 'artist:' in song.yaml)", file=sys.stderr)
        return 2
    tab = Path(args.tab).resolve() if args.tab else tab
    stem = Path(args.stem).resolve() if args.stem else stem
    cover = Path(args.cover).resolve() if args.cover else cover
    out = args.out or song.get("output_dir")
    inp = SongInputs(
        audio=audio, title=str(title), artist=str(artist), album=str(pick("album", "") or ""),
        year=int(pick("year", 0) or 0), tab=tab, track=pick("track"), stem=stem,
        tuning=pick("tuning"), strings=int(pick("strings", cfg["strings"]) or 4), cover=cover,
        grid=pick("grid"), bass_picked=True if args.picked else song.get("bass_picked"),
        version=str(song.get("version", "1")),
        output_dir=Path(out).resolve() if out else (folder / "output" if folder else None),
    )
    for p in (inp.audio, inp.tab, inp.stem, inp.cover):
        if p is not None and not Path(p).exists():
            print(f"File not found: {p}", file=sys.stderr)
            return 2
    try:
        run(inp, cfg, package=not args.no_package)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
