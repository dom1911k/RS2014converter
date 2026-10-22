"""Compare a generated arrangement XML with truth.json from make_synthetic.py.

  python tests/evaluate.py OUTPUT_SONG_FOLDER TRUTH_JSON
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

E_STD = [28, 33, 38, 43]


def load_chart(folder: Path):
    root = ET.parse(folder / "project" / "arr_bass_RS2.xml").getroot()
    tun = root.find("tuning")
    offsets = [int(tun.get(f"string{i}")) for i in range(4)]
    notes = []
    for n in root.iter("note"):
        s, f = int(n.get("string")), int(n.get("fret"))
        legato = n.get("hammerOn") == "1" or n.get("pullOff") == "1"
        notes.append((float(n.get("time")), E_STD[s] + offsets[s] + f, s, f, legato))
    report = (folder / "report.txt").read_text()
    m = re.search(r"; ([0-9.]+) s of silence was added", report)
    pad = float(m.group(1)) if m else 0.0
    beats = [float(b.get("time")) - pad for b in root.iter("ebeat")]
    load_chart.cent_offset = float(root.findtext("centOffset") or 0)
    return [(t - pad, p, s, f, lg) for t, p, s, f, lg in notes], beats


def evaluate(folder: Path, truth_path: Path, tol: float = 0.05) -> dict:
    truth = json.loads(truth_path.read_text())
    tnotes = truth["notes"]
    chart, beats = load_chart(folder)
    used = set()
    hits = pitch_ok = octave_err = legato_ok = 0
    errs = []
    for tn in tnotes:
        best = None
        for k, (t, p, s, f, lg) in enumerate(chart):
            if k in used:
                continue
            d = abs(t - tn["time"])
            if d <= tol and (best is None or d < best[0]):
                best = (d, k, p, lg)
        if best:
            used.add(best[1])
            hits += 1
            errs.append(best[0])
            if tn.get("legato") and best[3]:
                legato_ok += 1
            if best[2] == tn["pitch"]:
                pitch_ok += 1
            elif abs(best[2] - tn["pitch"]) == 12:
                octave_err += 1
    tb = np.array(truth["beats"])
    beat_err = [np.min(np.abs(np.array(beats) - t)) for t in tb]
    res = {
        "truth_notes": len(tnotes), "chart_notes": len(chart),
        "recall": hits / len(tnotes), "precision": hits / max(1, len(chart)),
        "pitch_accuracy": pitch_ok / max(1, hits), "octave_errors": octave_err,
        "mean_onset_error_ms": 1000 * float(np.mean(errs)) if errs else None,
        "beats_within_30ms": float(np.mean(np.array(beat_err) < 0.03)),
        "legato_marked": f"{legato_ok}/{sum(1 for n in tnotes if n.get('legato'))}",
        "cent_offset": load_chart.cent_offset,
    }
    return res


if __name__ == "__main__":
    r = evaluate(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps(r, indent=1))
