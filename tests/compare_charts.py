"""Compare a chart made without a tab against one made with a tab, for the same audio.

  python tests/compare_charts.py "output/Band - Song (with tab)" "output/Band - Song (no tab)"

The tab chart is the reference: prints how many notes were found, pitch accuracy, fingering
agreement and bar-line agreement. Useful to measure the no-tab transcription on real songs.
"""
import sys, glob, re
import numpy as np
from xml.etree import ElementTree as ET
from collections import Counter

E = [28, 33, 38, 43]
NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def load(folder):
    xml = glob.glob(folder + '/project/arr_bass_RS2.xml')[0]
    r = ET.parse(xml).getroot()
    rep = open(folder + '/report.txt', encoding='utf-8').read()
    m = re.search(r"; ([0-9.]+) s of silence was added", rep)
    pad = float(m.group(1)) if m else 0.0
    tun = r.find('tuning'); off = [int(tun.get(f'string{i}')) for i in range(4)]
    notes = [(float(n.get('time')) - pad, E[int(n.get('string'))] + off[int(n.get('string'))] + int(n.get('fret')),
              int(n.get('string')), int(n.get('fret'))) for n in r.findall('.//level/notes/note')]
    dbs = np.array([float(e.get('time')) - pad for e in r.iter('ebeat') if e.get('measure') is not None])
    return notes, dbs


ref, ref_db = load(sys.argv[1])
hyp, hyp_db = load(sys.argv[2])
ht = np.array([h[0] for h in hyp])
used = set(); hit = pitch = octave = same_pos = 0; misses = []; wrong = []
for t, p, s, f in ref:
    k = int(np.argmin(np.abs(ht - t))) if len(ht) else -1
    if k >= 0 and abs(ht[k] - t) < 0.06 and k not in used:
        used.add(k); hit += 1
        hp = hyp[k][1]
        if hp == p:
            pitch += 1
            same_pos += (hyp[k][2], hyp[k][3]) == (s, f)
        else:
            octave += abs(hp - p) == 12
            wrong.append((round(t, 2), NAMES[p % 12] + str(p // 12 - 1), NAMES[hp % 12] + str(hp // 12 - 1)))
    else:
        misses.append(round(t, 2))
extra = len(hyp) - len(used)
print(f"reference notes {len(ref)}, transcribed {len(hyp)}")
print(f"found {hit/len(ref)*100:.0f}%, extra notes {extra} ({extra/max(1,len(hyp))*100:.0f}% of transcribed)")
print(f"pitch correct {pitch/max(1,hit)*100:.0f}% of found (octave errors {octave}); same string/fret as tab {same_pos/max(1,pitch)*100:.0f}%")
db_ok = np.mean([np.min(np.abs(hyp_db - d)) < 0.06 for d in ref_db[(ref_db > ref[0][0] - 1) & (ref_db < ref[-1][0])]])
print(f"bar lines matching the tab's: {db_ok*100:.0f}%")
print("wrong pitches (time, tab, transcribed):", Counter((a, b) for _, a, b in wrong).most_common(8))
mb = Counter(int(t // 10) * 10 for t in misses); print("misses per 10 s:", sorted(mb.items()))
