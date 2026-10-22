"""Compare a chart made without a tab against one made with a tab, for the same audio.

  python tests/compare_charts.py "output/Band - Song (with tab)" "output/Band - Song (no tab)"

The tab chart is the reference: prints how many notes were found, pitch accuracy, fingering
agreement and bar-line agreement. Useful to measure the no-tab transcription on real songs.
"""

from __future__ import annotations

import glob
import re
import sys
from collections import Counter

import numpy as np
from xml.etree import ElementTree as ET

E = [28, 33, 38, 43]
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _name(p: int) -> str:
    return NAMES[p % 12] + str(p // 12 - 1)


def load(folder: str):
    xml = glob.glob(glob.escape(folder) + "/project/arr_bass_RS2.xml")[0]
    r = ET.parse(xml).getroot()
    rep = open(folder + "/report.txt", encoding="utf-8").read()
    m = re.search(r"; ([0-9.]+) s of silence was added", rep)
    pad = float(m.group(1)) if m else 0.0
    tun = r.find("tuning")
    off = [int(tun.get(f"string{i}")) for i in range(4)]
    notes = [(float(n.get("time")) - pad, E[int(n.get("string"))] + off[int(n.get("string"))] + int(n.get("fret")),
              int(n.get("string")), int(n.get("fret"))) for n in r.findall(".//level/notes/note")]
    dbs = np.array([float(e.get("time")) - pad for e in r.iter("ebeat") if e.get("measure") is not None])
    return notes, dbs


def compare(ref_folder: str, hyp_folder: str) -> dict:
    ref, ref_db = load(ref_folder)
    hyp, hyp_db = load(hyp_folder)
    if not ref:
        return {"error": "reference chart has no notes"}
    ht = np.array([h[0] for h in hyp])
    used: set[int] = set()
    hit = pitch = octave = same_pos = 0
    misses, wrong = [], []
    for t, p, s, f in ref:
        k = int(np.argmin(np.abs(ht - t))) if len(ht) else -1
        if k >= 0 and abs(ht[k] - t) < 0.06 and k not in used:
            used.add(k)
            hit += 1
            hp = hyp[k][1]
            if hp == p:
                pitch += 1
                same_pos += (hyp[k][2], hyp[k][3]) == (s, f)
            else:
                octave += abs(hp - p) == 12
                wrong.append((round(t, 2), _name(p), _name(hp)))
        else:
            misses.append(round(t, 2))
    span = (ref_db > ref[0][0] - 1) & (ref_db < ref[-1][0])
    db_ok = float(np.mean([np.min(np.abs(hyp_db - d)) < 0.06 for d in ref_db[span]])) if len(hyp_db) and span.any() else 0.0
    return {
        "reference_notes": len(ref),
        "transcribed_notes": len(hyp),
        "found": hit / len(ref),
        "extra": (len(hyp) - len(used)) / max(1, len(hyp)),
        "pitch_correct": pitch / max(1, hit),
        "octave_errors": octave,
        "same_string_fret": same_pos / max(1, pitch),
        "bar_lines": db_ok,
        "wrong_pitches": Counter((a, b) for _, a, b in wrong).most_common(8),
        "misses_per_10s": sorted(Counter(int(t // 10) * 10 for t in misses).items()),
    }


def describe(r: dict) -> str:
    if "error" in r:
        return r["error"]
    return "\n".join([
        f"reference notes {r['reference_notes']}, transcribed {r['transcribed_notes']}",
        f"found {r['found'] * 100:.0f}%, extra notes {r['extra'] * 100:.0f}% of transcribed",
        f"pitch correct {r['pitch_correct'] * 100:.0f}% of found (octave errors {r['octave_errors']}); "
        f"same string/fret as tab {r['same_string_fret'] * 100:.0f}%",
        f"bar lines matching the tab's: {r['bar_lines'] * 100:.0f}%",
        f"wrong pitches (tab -> transcribed): {r['wrong_pitches']}",
        f"misses per 10 s: {r['misses_per_10s']}",
    ])


if __name__ == "__main__":
    print(describe(compare(sys.argv[1], sys.argv[2])))
