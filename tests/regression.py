"""End-to-end regression run on synthetic songs (needs the models; takes a few minutes on CPU).

  python tests/regression.py WORKDIR

Generates the test songs (if missing), runs make_cdlc on each scenario without packaging and
prints one line of scores per scenario.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import evaluate  # noqa: E402

SONGS = {
    "base": [],
    "weak": ["--fundamental", "0.12", "--lead", "0.4"],
    "shift": ["--tuning-shift", "-1"],
    "extra": ["--form", "AABBBCDAA", "--lead", "0.15"],
    "human": ["--humanize", "0.025", "--cents", "-30", "--legato"],
}

SCENARIOS = [  # name, song, use tab, use given stem
    ("tab + stem", "base", True, True),
    ("no tab + stem", "base", False, True),
    ("no tab, weak fundamental", "weak", False, True),
    ("tab, demucs, recording -1 semitone", "shift", True, False),
    ("tab + stem, extra bar in recording", "extra", True, True),
    ("tab + stem, humanized, A=432", "human", True, True),
    ("no tab + stem, humanized, legato, A=432", "human", False, True),
    ("no tab, demucs", "base", False, False),
]


def main() -> int:
    work = Path(sys.argv[1]).resolve()
    py = sys.executable
    for name, args in SONGS.items():
        d = work / f"song_{name}"
        if not (d / "truth.json").exists():
            subprocess.run([py, str(HERE / "make_synthetic.py"), str(d), *args], check=True, capture_output=True)
    rows = []
    for title, song, use_tab, use_stem in SCENARIOS:
        d = work / f"song_{song}"
        out = work / "out"
        safe = title.replace(",", "").replace("+", "and").replace("=", "")
        cmd = [py, str(ROOT / "make_cdlc.py"), str(d / "mix.wav"), "--title", safe, "--artist", "Test",
               "--out", str(out), "--no-package"]
        if use_tab:
            cmd += ["--tab", str(d / "tab.gp5")]
        if use_stem:
            cmd += ["--stem", str(d / "bass.wav")]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0:
            rows.append((title, {"error": p.stderr.strip().splitlines()[-1] if p.stderr else "failed"}))
            continue
        rows.append((title, evaluate.evaluate(out / f"Test - {safe}", d / "truth.json")))
    for title, r in rows:
        if "error" in r:
            print(f"{title:45s} ERROR {r['error']}")
            continue
        print(f"{title:45s} recall {r['recall']:.2f}  precision {r['precision']:.2f}  pitch {r['pitch_accuracy']:.2f}  "
              f"onset {r['mean_onset_error_ms']:.0f} ms  beats {r['beats_within_30ms']:.2f}  "
              f"legato {r['legato_marked']}  cents {r['cent_offset']:+.0f}")
    (work / "regression.json").write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
