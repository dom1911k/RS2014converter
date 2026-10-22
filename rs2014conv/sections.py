"""Song sections: from tab markers if available, otherwise a structural segmentation of the mix.

Rocksmith only accepts a fixed list of section names, so free text is mapped onto it.
"""

from __future__ import annotations

import re

import numpy as np

from . import audio_io
from .beats import BeatMap
from .log import info
from .model import Beat, Note, Section

VALID = {"fadein", "fadeout", "buildup", "chorus", "hook", "head", "bridge", "ambient", "breakdown",
         "interlude", "intro", "melody", "modbridge", "modchorus", "modverse", "outro", "postbrdg",
         "postchorus", "postvs", "prebrdg", "prechorus", "preverse", "riff", "silence", "solo",
         "transition", "vamp", "variation", "verse", "tapping", "noguitar"}

_KEYWORDS = [
    (r"pre.?chorus|pre.?refrain|pre.?ch\b", "prechorus"),
    (r"post.?chorus|post.?refrain", "postchorus"),
    (r"pre.?verse", "preverse"),
    (r"post.?verse", "postvs"),
    (r"pre.?bridge", "prebrdg"),
    (r"post.?bridge", "postbrdg"),
    (r"chorus|refrain|ritornell|estribillo", "chorus"),
    (r"verse|strophe|vers\b|couplet|estrofa", "verse"),
    (r"bridge|br[uü]cke|puente|pont\b|middle ?8", "bridge"),
    (r"solo", "solo"),
    (r"intro|count|start", "intro"),
    (r"outro|ending|\bend\b|coda|fine|finale", "outro"),
    (r"interlude|instrumental|zwischenspiel", "interlude"),
    (r"break ?down", "breakdown"),
    (r"break", "breakdown"),
    (r"fade ?out", "fadeout"),
    (r"fade ?in", "fadein"),
    (r"hook", "hook"),
    (r"build", "buildup"),
    (r"riff|groove|theme|main", "riff"),
    (r"tap", "tapping"),
    (r"vamp", "vamp"),
    (r"silence|tacet|rest", "noguitar"),
]


def normalize(name: str) -> str:
    key = name.strip().lower()
    compact = re.sub(r"[^a-z]", "", key)
    if compact in VALID:
        return compact
    for pat, rs in _KEYWORDS:
        if re.search(pat, key):
            return rs
    return "riff"


def from_markers(markers: list[Section]) -> list[Section]:
    out = []
    for s in markers:
        out.append(Section(normalize(s.name), s.time))
    return out


def auto_detect(mix_path, bm: BeatMap, notes: list[Note], min_bars: int = 4,
                boundaries: list[float] | None = None) -> list[Section]:
    """Segment the song at downbeats using a self-similarity novelty curve (or at the given
    boundary times, e.g. where the tab's bass pattern starts a new block), then name the parts."""
    import librosa

    sr = 22050
    y = audio_io.load(mix_path, sr=sr, mono=True)[0]
    hop = 512
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    mfcc = librosa.feature.mfcc(y=y, sr=sr, hop_length=hop, n_mfcc=13)
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    downbeats = bm.times[bm.downbeat]
    if len(downbeats) < 2 * min_bars:
        return []
    frames = np.clip(librosa.time_to_frames(downbeats, sr=sr, hop_length=hop), 0, chroma.shape[1] - 1)
    feat = np.vstack([librosa.util.normalize(chroma, axis=0), librosa.util.normalize(mfcc, axis=1)])
    bars = librosa.util.sync(feat, frames, aggregate=np.median)  # one column per bar
    bar_rms = librosa.util.sync(rms[None], frames, aggregate=np.mean)[0]
    nbar = bars.shape[1]
    X = bars / (np.linalg.norm(bars, axis=0, keepdims=True) + 1e-9)
    S = X.T @ X
    # Checkerboard novelty with a 4-bar kernel (boundaries must then be min_bars apart)
    k = 4
    sign = np.ones((2 * k, 2 * k))
    sign[:k, k:] = -1
    sign[k:, :k] = -1
    Sp = np.pad(S, k, mode="edge")
    nov = np.array([np.sum(Sp[i:i + 2 * k, i:i + 2 * k] * sign) for i in range(nbar)])
    nov = np.maximum(nov, 0)
    picks: list[int] = []
    if boundaries:
        for t in boundaries:
            i = int(np.argmin(np.abs(downbeats - t)))
            if 0 < i < nbar:
                picks.append(i)
    else:
        # Novelty peaks at least min_bars apart.
        order = np.argsort(-nov)
        thr = np.percentile(nov, 70)
        for i in order:
            if nov[i] < thr:
                break
            if i < min_bars or i > nbar - min_bars:
                continue
            if all(abs(i - p) >= min_bars for p in picks):
                picks.append(int(i))
    bounds = sorted(set([0] + picks))
    # Note activity per bar
    note_times = np.array([n.time for n in notes]) if notes else np.array([])
    seg_ranges = [(b, (bounds[i + 1] if i + 1 < len(bounds) else nbar)) for i, b in enumerate(bounds)]

    # Naming. Unique parts (unlike every other part) are bridges/interludes; among the parts
    # that recur, the louder ones are choruses and the quieter ones verses.
    means = [X[:, a:b].mean(1) for a, b in seg_ranges]
    M = np.array([m / (np.linalg.norm(m) + 1e-9) for m in means])
    sim = M @ M.T
    np.fill_diagonal(sim, -1)
    seg_db = np.array([20 * np.log10(float(bar_rms[a:b].mean()) + 1e-9) for a, b in seg_ranges])
    note_times = np.array([n.time for n in notes]) if notes else np.array([])
    first_note = note_times.min() if len(note_times) else 0.0
    last_note = note_times.max() if len(note_times) else 1e9
    playing = [i for i, (a, b) in enumerate(seg_ranges)
               if len(note_times) and np.any((note_times >= downbeats[a]) &
                                             (note_times < (downbeats[b] if b < len(downbeats) else 1e9)))]
    unique = {i for i in playing if len(seg_ranges) > 2 and sim[i].max() < 0.88}
    recurring = [i for i in playing if i not in unique]
    # Modern masters are loud throughout: compare the parts with each other (median split). If they
    # hardly differ (< 1 dB), don't pretend to know verse from chorus.
    loud_cut = float(np.median(seg_db[recurring])) if recurring else 0.0
    spread = float(np.ptp(seg_db[recurring])) if len(recurring) > 1 else 0.0
    names: dict[int, str] = {}
    n_unique = 0
    for i in playing:
        if i in unique:
            if i == playing[-1]:
                names[i] = "outro"
            else:
                names[i] = "bridge" if n_unique == 0 else "interlude"
                n_unique += 1
        elif spread < 1.0:
            names[i] = "riff"
        else:
            names[i] = "chorus" if seg_db[i] >= loud_cut - 1e-6 else "verse"
    labels = list(range(len(seg_ranges)))

    sections: list[Section] = []
    for (a, b), lab in zip(seg_ranges, labels):
        t0 = float(downbeats[a])
        t1 = float(downbeats[b]) if b < len(downbeats) else bm.times[-1]
        has_notes = bool(np.any((note_times >= t0) & (note_times < t1))) if len(note_times) else False
        if t1 <= first_note + 1e-3 and not has_notes:
            name = "intro"
        elif t0 > last_note:
            name = "outro"
        else:
            name = names.get(lab, "riff") if has_notes else "riff"
        if name == "solo":
            name = "riff"
        sections.append(Section(name, t0))
    # The first section containing the first note: if nothing before it, call it intro
    merged: list[Section] = []
    for s in sections:
        if merged and merged[-1].name == s.name and s.name in ("intro", "outro"):
            continue
        merged.append(s)
    info("Sections (auto): " + ", ".join(f"{s.name}@{s.time:.1f}s" for s in merged))
    return merged


def finalize(sections: list[Section], beats: list[Beat], notes: list[Note]) -> list[Section]:
    """Snap to downbeats, make sure the first note is inside a real section, add the final 'noguitar'."""
    downbeats = np.array([b.time for b in beats if b.measure >= 0])
    if not notes:
        return [Section("noguitar", downbeats[0] if len(downbeats) else 0.0)]
    first = min(n.time for n in notes)
    last = max(n.end for n in notes)

    def snap(t: float) -> float:
        return float(downbeats[np.argmin(np.abs(downbeats - t))])

    secs = sorted([Section(s.name, snap(s.time)) for s in sections if s.name != "noguitar"],
                  key=lambda s: s.time)
    # The first section must start at or before the first note, on a downbeat after the first beat
    # (the very first beat belongs to the empty COUNT phrase).
    db_before_first = downbeats[downbeats <= first + 1e-3]
    first_start = float(db_before_first[-1]) if len(db_before_first) else float(downbeats[0])
    if first_start <= beats[0].time + 1e-3:
        # No room for an empty count-in phrase before the first note's measure: use the next beat.
        later = [b.time for b in beats if b.time > beats[0].time + 1e-3 and b.time <= first + 1e-3]
        first_start = later[0] if later else first_start
    secs = [s for s in secs if s.time > first_start + 1e-3 and s.time < last]
    # The section in effect where the bass comes in: the latest one starting at/before the first note.
    started = [x for x in sorted(sections, key=lambda x: x.time) if x.time <= first + 1e-3 and x.name != "noguitar"]
    lead_name = started[-1].name if started else "intro"
    # A long intro without bass (4+ bars) gets its own empty "intro" section, so the count-in
    # (COUNT phrase) is only the first bar and the song map shows where the bass comes in.
    empty_intro = downbeats[(downbeats > beats[0].time + 1e-3) & (downbeats < first_start - 1e-3)]
    if len(empty_intro) >= 3:
        secs.insert(0, Section("intro", float(empty_intro[0])))
    if not (len(empty_intro) >= 3 and lead_name == "intro"):  # else the intro just continues
        secs.append(Section(lead_name, first_start))

    # Breaks of 2+ bars without bass become "noguitar" sections; the section that was playing
    # resumes when the bass comes back.
    ordered = sorted(notes, key=lambda n: n.time)
    secs.sort(key=lambda x: x.time)
    for a, b in zip(ordered, ordered[1:]):
        inside = downbeats[(downbeats > a.end + 0.05) & (downbeats < b.time - 0.05)]
        if len(inside) < 2:
            continue
        gap_start = float(inside[0])
        resume_candidates = downbeats[downbeats <= b.time + 1e-3]
        resume = float(resume_candidates[-1])
        # Name to resume with: the latest section that started before the bass came back.
        before = [x for x in secs if x.time <= resume + 1e-3 and x.name != "noguitar"]
        resume_name = before[-1].name if before else lead_name
        if resume_name in ("intro", "noguitar"):
            resume_name = "riff"
        # Sections that would start inside the break are dropped; one starting right at the
        # resume point wins over the resumed name.
        at_resume = [x for x in secs if abs(x.time - resume) < 1e-3]
        secs = [x for x in secs if not (gap_start - 1e-3 < x.time < resume + 1e-3)]
        secs.append(Section("noguitar", gap_start))
        secs.append(at_resume[0] if at_resume else Section(resume_name, resume))
        secs.sort(key=lambda x: x.time)
    # Merge sections starting at the same time (the later entry wins), and drop sections that
    # would last less than 2 bars (except breaks, which are at least 2 bars by construction).
    out: list[Section] = []
    for s in secs:
        if out and abs(out[-1].time - s.time) < 1e-3:
            out[-1] = s
            continue
        if out and s.name != "noguitar" and out[-1].name != "noguitar":
            bars_between = int(np.sum((downbeats >= out[-1].time - 1e-3) & (downbeats < s.time - 1e-3)))
            if bars_between < 2:
                out[-1] = Section(out[-1].name, out[-1].time)
                continue
        out.append(s)
    # Final noguitar section on the first downbeat after the last note ends.
    after = downbeats[downbeats > last + 0.05]
    end_t = float(after[0]) if len(after) else float(beats[-1].time)
    out.append(Section("noguitar", end_t))
    if len(out) > 99:
        out = out[:98] + [out[-1]]
    return out
