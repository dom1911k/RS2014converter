"""Measure how well make_cdlc works on your own songs - without sending any audio.

  python tools/evaluate_library.py "C:\\Songs"            (or drag the folder onto evaluate_songs.bat)

"C:\\Songs" contains one folder per song, set up like for make_cdlc (audio + song.yaml, and a
Guitar Pro tab where you have one). For every song:
  * with a tab: the chart is made twice - from the tab, and from the audio alone - and the
    audio-only chart is scored against the tab chart (notes found, pitch, fingering, bar lines);
  * without a tab: the audio-only chart is made, and the report is collected.

Everything ends up in evaluation\\evaluation_results.zip: scores, reports, the charts (XML) and the
tabs, but no audio - typically well under 1 MB per song. Send that zip to whoever improves the
code. Nothing is packaged into .psarc files (that needs no testing per song and takes time).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

AUDIO_EXT = {".flac", ".wav", ".mp3", ".ogg", ".m4a", ".aac", ".opus", ".wma", ".aiff", ".aif"}
TAB_EXT = {".gp3", ".gp4", ".gp5", ".gpx", ".gp"}


def song_folders(library: Path) -> list[Path]:
    out = []
    for d in sorted(p for p in library.iterdir() if p.is_dir()):
        if d.name.lower() in ("evaluation", "output"):
            continue
        if any(p.suffix.lower() in AUDIO_EXT for p in d.iterdir()):
            out.append(d)
    return out


def has_tab(folder: Path) -> bool:
    import yaml

    y = folder / "song.yaml"
    if y.exists():
        data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
        if data.get("tab"):
            return True
    return any(p.suffix.lower() in TAB_EXT for p in folder.iterdir())


def run(folder: Path, out: Path, no_tab: bool, log: Path) -> tuple[Path | None, float, str]:
    cmd = [sys.executable, str(ROOT / "make_cdlc.py"), str(folder), "--out", str(out), "--no-package"]
    if no_tab:
        cmd.append("--no-tab")
    t0 = time.time()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, text=True)
    took = time.time() - t0
    results = [d for d in out.iterdir() if d.is_dir()] if out.exists() else []
    if p.returncode != 0 or not results:
        tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] or ["failed"]
        return None, took, tail[0]
    return results[0], took, ""


def report_facts(result: Path | None) -> dict:
    if result is None or not (result / "report.txt").exists():
        return {}
    text = (result / "report.txt").read_text(encoding="utf-8")

    def find(pattern: str) -> str:
        m = re.search(pattern, text)
        return m.group(1).strip() if m else ""

    return {
        "beat_tracker": find(r"Beat tracker: (\w+)"),
        "tempo": find(r"median tempo ([0-9.]+)"),
        "tab_match": find(r"match quality ([0-9.]+)"),
        "flagged_measures": str(len(re.findall(r"^  \d+:\d\d\.\d+ - ", text, re.M))),
        "tuning": find(r"^Tuning: (.*)$"),
        "timing": find(r"^Timing: (.*)$"),
        "notes": find(r"^Notes: (\d+)"),
        "sections": find(r"^Sections: (.*)$"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Score make_cdlc on your own songs (no audio leaves your PC)")
    ap.add_argument("library", help="folder with one sub-folder per song")
    ap.add_argument("--out", help="where to put the results (default: <library>\\evaluation)")
    ap.add_argument("--no-tabs-in-zip", action="store_true", help="don't include the tab files in the zip")
    args = ap.parse_args()

    from compare_charts import compare, describe

    library = Path(args.library).resolve()
    out = Path(args.out).resolve() if args.out else library / "evaluation"
    out.mkdir(parents=True, exist_ok=True)
    songs = song_folders(library)
    if not songs:
        print(f"No song folders with audio found in {library}")
        return 1
    print(f"{len(songs)} song folder(s). This takes a while (each song is analysed once or twice).\n")

    rows = []
    for i, folder in enumerate(songs, 1):
        name = folder.name
        work = out / "runs" / re.sub(r'[<>:"/\\|?*]+', "_", name)
        print(f"[{i}/{len(songs)}] {name}")
        work.mkdir(parents=True, exist_ok=True)
        row = {"song": name, "has_tab": has_tab(folder)}
        tab_result = None
        if row["has_tab"]:
            tab_result, took, err = run(folder, work / "with_tab", False, work / "with_tab.log")
            row["time_tab_s"] = round(took)
            row["error_tab"] = err
            row.update({f"tab_{k}": v for k, v in report_facts(tab_result).items()})
            print(f"    with tab: {'OK' if tab_result else 'FAILED: ' + err}")
        notab_result, took, err = run(folder, work / "no_tab", True, work / "no_tab.log")
        row["time_notab_s"] = round(took)
        row["error_notab"] = err
        row.update({f"notab_{k}": v for k, v in report_facts(notab_result).items()})
        print(f"    audio only: {'OK' if notab_result else 'FAILED: ' + err}")
        if tab_result and notab_result:
            try:
                c = compare(str(tab_result), str(notab_result))
                (work / "score.txt").write_text(describe(c), encoding="utf-8")
                for k in ("found", "extra", "pitch_correct", "same_string_fret", "bar_lines"):
                    row[k] = round(c[k] * 100) if k in c else ""
                row["octave_errors"] = c.get("octave_errors", "")
                print(f"    score: found {row['found']}%, extra {row['extra']}%, pitch {row['pitch_correct']}%, "
                      f"bar lines {row['bar_lines']}%")
            except Exception as e:  # noqa: BLE001
                row["error_compare"] = str(e)
        rows.append(row)

    # Summary table
    keys = []
    for r in rows:
        keys += [k for k in r if k not in keys]
    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    scored = [r for r in rows if isinstance(r.get("found"), int)]
    lines = [f"make_cdlc evaluation - {_dt.datetime.now():%Y-%m-%d %H:%M} - {len(rows)} songs, {len(scored)} scored", ""]
    for r in rows:
        if isinstance(r.get("found"), int):
            lines.append(f"{r['song'][:40]:40s} found {r['found']:3d}%  extra {r['extra']:3d}%  pitch {r['pitch_correct']:3d}%  "
                         f"fingering {r['same_string_fret']:3d}%  bar lines {r['bar_lines']:3d}%  tab match {r.get('tab_tab_match', '')}")
        else:
            errs = "; ".join(v for k, v in r.items() if k.startswith("error") and v)
            lines.append(f"{r['song'][:40]:40s} {'ERROR: ' + errs if errs else 'no tab - audio-only chart made'}")
    if scored:
        avg = {k: sum(r[k] for r in scored) / len(scored) for k in ("found", "extra", "pitch_correct", "bar_lines")}
        lines += ["", "Average over scored songs: " + ", ".join(f"{k} {v:.0f}%" for k, v in avg.items())]
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))

    # Results zip: text files, charts and tabs - no audio.
    zpath = out / "evaluation_results.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(out / "summary.csv", "summary.csv")
        z.write(out / "summary.txt", "summary.txt")
        for f in (out / "runs").rglob("*"):
            rel = f.relative_to(out / "runs")
            if f.is_file() and (f.suffix in (".txt", ".log") or f.name == "arr_bass_RS2.xml"):
                z.write(f, "runs/" + str(rel).replace("\\", "/"))
        if not args.no_tabs_in_zip:
            for folder in songs:
                for f in folder.iterdir():
                    if f.suffix.lower() in TAB_EXT or f.name == "song.yaml":
                        z.write(f, f"tabs/{folder.name}/{f.name}")
    size = zpath.stat().st_size / 1e6
    print(f"\nResults: {zpath}  ({size:.1f} MB, no audio inside)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
