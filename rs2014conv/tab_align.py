"""Align a (repeat-expanded) tab to the recording.

1. The tab is unrolled into a linear sequence of measures/beats (repeats, endings, D.C. ...).
2. Per beat, both the tab and the bass stem are summarised as short feature vectors
   (bass pitch class + note attacks in 4 sub-slots).
3. Dynamic time warping on whole beats maps tab beats to audio beats. Several readings of the
   audio beat grid are tried (as detected, double, half) to absorb tracker tempo-octave errors.
4. Tab beats are given the time of their matched audio beat; everything inside a beat is
   interpolated, so notes follow the real (drifting) tempo of the recording.
5. Measures where the match is poor or the warping is not 1:1 are flagged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import audio_io
from .beats import BeatMap
from .log import info, warn
from .model import Beat, Note, Section, TimeSignatureEvent
from .tabs import TabMeasure, TabSong

SUB = 4  # sub-slots per beat in the features
SR = 22050


@dataclass
class LinearTab:
    measures: list[TabMeasure]  # in playing order
    measure_start_beat: list[int]  # beat index (tab beats) where each played measure starts
    beat_q: np.ndarray  # quarter-note position (linear time) of each tab beat
    notes: list[tuple[float, float, object]]  # (linear q start, q duration, TabNote)
    total_beats: int


def unroll(song: TabSong) -> LinearTab:
    order = song.playback()
    measures, mstart, beat_q, notes = [], [], [], []
    q = 0.0
    b = 0
    for idx in order:
        m = song.measures[idx]
        measures.append(m)
        mstart.append(b)
        for k in range(m.beats):
            beat_q.append(q + k * m.beat_length)
        for n in m.notes:
            notes.append((q + (n.start - m.start), n.duration, n))
        q += m.length
        b += m.beats
    beat_q.append(q)  # end sentinel
    if len(order) != len(song.measures):
        info(f"Tab repeats expanded: {len(song.measures)} written measures -> {len(order)} played measures")
    return LinearTab(measures, mstart, np.array(beat_q), notes, b)


def _q_to_beat(lt: LinearTab, q: float | np.ndarray) -> np.ndarray:
    return np.interp(q, lt.beat_q, np.arange(len(lt.beat_q), dtype=float))


# --------------------------------------------------------------------------- features

def _tab_features(lt: LinearTab, tuning: list[int], shift: int = 0) -> np.ndarray:
    F = np.zeros((lt.total_beats, SUB, 13))
    for qs, qd, n in lt.notes:
        if n.tie:
            continue
        b0 = float(_q_to_beat(lt, qs))
        b1 = float(_q_to_beat(lt, qs + max(qd, 0.05)))
        pitch = tuning[n.string] + n.fret + shift if 0 <= n.string < len(tuning) else None
        s0 = int(np.floor(b0 * SUB + 1e-6))
        s1 = max(s0 + 1, int(np.ceil(b1 * SUB - 1e-6)))
        for s in range(s0, min(s1, lt.total_beats * SUB)):
            if pitch is not None and not n.dead:
                F[s // SUB, s % SUB, pitch % 12] += 1.0
        if s0 < lt.total_beats * SUB:
            F[s0 // SUB, s0 % SUB, 12] += 1.0
    F[..., :12] = np.minimum(F[..., :12], 1.0)
    F[..., 12] = np.minimum(F[..., 12], 1.0) * 1.2
    return F.reshape(lt.total_beats, -1)


class StemFeatures:
    def __init__(self, stem_path):
        import librosa

        y = audio_io.load(stem_path, sr=SR, mono=True)[0]
        y = y / (np.abs(y).max() + 1e-9)
        self.hop = 256
        C = np.abs(librosa.cqt(y, sr=SR, hop_length=self.hop, fmin=librosa.midi_to_hz(24), n_bins=48,
                               bins_per_octave=12))
        C = np.log1p(20 * C / (C.max() + 1e-9))
        self.chroma = np.zeros((12, C.shape[1]))
        for k in range(48):
            self.chroma[(24 + k) % 12] += C[k]
        on = librosa.onset.onset_strength(y=y, sr=SR, hop_length=self.hop)
        self.onset = on / (np.percentile(on, 99) + 1e-9)
        self.energy = C.sum(0)
        self.silence = self.energy < 0.05 * np.percentile(self.energy, 90)

    def frame(self, t: np.ndarray) -> np.ndarray:
        return np.clip((np.asarray(t) * SR / self.hop).astype(int), 0, self.chroma.shape[1] - 1)

    def features(self, grid: np.ndarray) -> np.ndarray:
        """Features for each beat interval of the grid (len(grid)-1 beats)."""
        nb = len(grid) - 1
        F = np.zeros((nb, SUB, 13))
        for j in range(nb):
            edges = np.linspace(grid[j], grid[j + 1], SUB + 1)
            fr = self.frame(edges)
            for s in range(SUB):
                a, b = fr[s], max(fr[s] + 1, fr[s + 1])
                ch = self.chroma[:, a:b].mean(1)
                if self.silence[a:b].mean() > 0.6:
                    ch = np.zeros(12)
                else:
                    ch = ch / (ch.max() + 1e-9)
                    ch = np.where(ch > 0.6, ch, 0.0)
                F[j, s, :12] = ch
                w0, w1 = max(0, a - 3), a + 4
                F[j, s, 12] = 1.2 * min(1.0, float(self.onset[w0:w1].max()) if w1 > w0 else 0.0)
        return F.reshape(nb, -1)


def _cost_matrix(T: np.ndarray, A: np.ndarray) -> np.ndarray:
    tn = np.linalg.norm(T, axis=1, keepdims=True)
    an = np.linalg.norm(A, axis=1, keepdims=True)
    sim = (T / np.maximum(tn, 1e-9)) @ (A / np.maximum(an, 1e-9)).T
    # Silent tab beat vs silent audio beat is a good match; silence vs notes is a bad one.
    t_empty = tn[:, 0] < 1e-6
    a_empty = an[:, 0] < 1e-6
    sim[np.ix_(t_empty, a_empty)] = 0.7
    sim[np.ix_(t_empty, ~a_empty)] = 0.2
    sim[np.ix_(~t_empty, a_empty)] = 0.0
    return 1.0 - sim


def _dtw(C: np.ndarray, skip_audio: float = 0.35, double: float = 1.0) -> tuple[list[tuple[int, int]], float]:
    """Subsequence DTW: the tab must be matched completely, audio may start/end anywhere.

    Steps (tab, audio): (1,1) normal, (1,2)/(2,1) local tempo halving/doubling,
    (0,1) skip an audio beat (the recording has beats the tab doesn't).
    """
    T, A = C.shape
    INF = 1e18
    D = np.full((T, A), INF)
    P = np.zeros((T, A), np.int8)  # 0 diag, 1 (1,2), 2 (2,1), 3 skip audio
    D[0] = C[0]
    ar = np.arange(A)
    for i in range(1, T):
        diag = np.full(A, INF)
        diag[1:] = D[i - 1, :-1]
        a12 = np.full(A, INF)
        a12[2:] = D[i - 1, :-2] + double + C[i, 1:-1]
        a21 = np.full(A, INF)
        if i >= 2:
            a21[1:] = D[i - 2, :-1] + double + C[i - 1, 1:]
        stack = np.vstack([diag, a12, a21])
        best = stack.argmin(0)
        row = stack[best, ar] + C[i]
        # Horizontal steps: row[j] = min_k<=j row[k] + skip * (j - k)  (vectorised)
        run = np.minimum.accumulate(row - skip_audio * ar) + skip_audio * ar
        improved = run < row - 1e-12
        best[improved] = 3
        D[i] = np.minimum(row, run)
        P[i] = best
    j = int(np.argmin(D[-1]))
    total = float(D[-1, j])
    path: list[tuple[int, int]] = []
    i = T - 1
    while True:
        while i > 0 and P[i, j] == 3:
            j -= 1
        path.append((i, j))
        if i == 0:
            break
        step = P[i, j]
        if step == 0:
            i, j = i - 1, j - 1
        elif step == 1:
            path.append((i, j - 1))
            i, j = i - 1, j - 2
        else:
            path.append((i - 1, j))
            i, j = i - 2, j - 1
            if i < 0:
                break
    path.reverse()
    return path, total / T


@dataclass
class Alignment:
    tab_beat_times: np.ndarray  # audio time of each tab beat (+ end sentinel)
    grid_name: str
    cost: float
    beat_cost: np.ndarray  # local cost per tab beat
    pitch_shift: int
    flagged: list[tuple[float, float, str]]
    grid: np.ndarray | None = None  # the audio beat grid that was used


def _grid_variants(bm: BeatMap) -> list[tuple[str, np.ndarray]]:
    t = bm.times
    mids = (t[:-1] + t[1:]) / 2
    double = np.empty(len(t) + len(mids))
    double[0::2] = t
    double[1::2] = mids
    return [("as detected", t), ("double tempo", double), ("half tempo (A)", t[0::2]), ("half tempo (B)", t[1::2])]


def align(lt: LinearTab, tuning: list[int], stem_path, bm: BeatMap, try_shifts: bool = True) -> Alignment:
    first_note_q = min(q for q, _, n in lt.notes)
    last_note_q = max(q + d for q, d, n in lt.notes)
    b_first = int(np.floor(float(_q_to_beat(lt, first_note_q))))
    b_last = int(np.ceil(float(_q_to_beat(lt, last_note_q))))
    b_last = min(max(b_last, b_first + 1), lt.total_beats)

    feats = StemFeatures(stem_path)
    T_full = _tab_features(lt, tuning, 0)
    T = T_full[b_first:b_last]

    best = None
    for name, grid in _grid_variants(bm):
        if len(grid) < 4:
            continue
        A = feats.features(grid)
        C = _cost_matrix(T, A)
        if C.shape[1] < C.shape[0] // 2:
            continue
        path, cost = _dtw(C)
        info(f"  alignment with audio beat grid '{name}': cost {cost:.3f}")
        if best is None or cost < best[1] - 1e-9:
            best = (name, cost, path, grid, A, C)
    if best is None:
        raise RuntimeError("Could not align the tab to the audio (audio too short?)")
    name, cost, path, grid, A, C = best

    # Tuning / transposition check: does the recording match the tab shifted by some semitones?
    shift = 0
    if try_shifts:
        scores = {}
        for s in range(-5, 6):
            Ts = _tab_features(lt, tuning, s)[b_first:b_last]
            Cs = _cost_matrix(Ts, A)
            scores[s] = float(np.mean([Cs[i, j] for i, j in path]))
        best_s = min(scores, key=scores.get)
        if best_s != 0 and scores[best_s] < scores[0] - 0.08:
            shift = best_s
            warn(f"The recording sounds {abs(shift)} semitone(s) {'lower' if shift < 0 else 'higher'} than the tab "
                 f"(match {1 - scores[0]:.2f} -> {1 - scores[best_s]:.2f}). The chart keeps the tab's frets and the "
                 f"tuning is adjusted by {shift:+d}. Use --tuning to override.")
            Ts = _tab_features(lt, tuning, shift)[b_first:b_last]
            C = _cost_matrix(Ts, A)
            path, cost = _dtw(C)

    # Map each tab beat (within the aligned range) to an audio time.
    t_of = {}
    for i, j in path:
        t_of.setdefault(i, []).append(grid[j])
    nb = b_last - b_first
    local_times = np.array([min(t_of[i]) if i in t_of else np.nan for i in range(nb)])
    # Two tab beats on one audio beat: spread them evenly.
    for i in range(1, nb):
        if np.isfinite(local_times[i]) and np.isfinite(local_times[i - 1]) and local_times[i] <= local_times[i - 1]:
            local_times[i] = np.nan
    idx = np.flatnonzero(np.isfinite(local_times))
    local_times = np.interp(np.arange(nb), idx, local_times[idx])
    # End of the last beat: the next audio beat after the last matched one.
    last_j = path[-1][1]
    end_t = grid[last_j + 1] if last_j + 1 < len(grid) else local_times[-1] + np.median(np.diff(local_times[-8:]) if nb > 2 else [0.5])

    # Extend to all tab beats (before the first note / after the last) using audio beats.
    times = np.full(lt.total_beats + 1, np.nan)
    times[b_first:b_last] = local_times
    times[b_last] = end_t
    j0 = path[0][1]
    for k in range(1, b_first + 1):  # backwards
        jj = j0 - k
        times[b_first - k] = grid[jj] if jj >= 0 else times[b_first - k + 1] - (grid[1] - grid[0])
    jl = last_j + 1
    for k in range(b_last + 1, lt.total_beats + 1):
        jl += 1
        times[k] = grid[jl] if jl < len(grid) else times[k - 1] + (grid[-1] - grid[-2])

    # Local cost per tab beat and flags.
    beat_cost = np.full(lt.total_beats, np.nan)
    extra_audio = np.zeros(nb)  # audio beats skipped right after this tab beat
    squeezed = np.zeros(nb)  # tab beats sharing an audio beat / spanning two
    seen: dict[int, list[int]] = {}
    for i, j in path:
        seen.setdefault(i, []).append(j)
    for i in range(nb):
        js = seen.get(i, [])
        if js:
            beat_cost[b_first + i] = float(np.mean([C[i, j] for j in js]))
            squeezed[i] = len(js) - 1
    for a, b in zip(path, path[1:]):
        if b[0] == a[0] + 1 and b[1] == a[1]:
            squeezed[a[0]] += 1
        if b[1] - a[1] > 1 and b[0] == a[0] + 1:
            extra_audio[a[0]] += b[1] - a[1] - 1
    # Audio beats skipped inside a horizontal run are visible as gaps between consecutive tab beats.

    flagged = []
    finite = beat_cost[np.isfinite(beat_cost)]
    med = float(np.median(finite)) if len(finite) else 0.0
    mad = float(np.median(np.abs(finite - med))) + 1e-3 if len(finite) else 0.1
    for mi, (m, mb) in enumerate(zip(lt.measures, lt.measure_start_beat)):
        sl = slice(mb, mb + m.beats)
        mc = beat_cost[sl]
        mc = mc[np.isfinite(mc)]
        if not len(mc):
            continue
        reasons = []
        if mc.mean() > max(0.55, med + 4 * mad):
            reasons.append(f"tab and audio don't match well (cost {mc.mean():.2f})")
        rel = slice(max(0, mb - b_first), max(0, mb - b_first + m.beats))
        if extra_audio[rel].sum() >= 1:
            reasons.append(f"the recording has {int(extra_audio[rel].sum())} beat(s) here that are not in the tab "
                           "(nothing is charted for them)")
        if squeezed[rel].sum() >= 1:
            reasons.append("tab beats had to be squeezed/stretched onto the recording's beats")
        if reasons:
            flagged.append((float(times[mb]), float(times[min(mb + m.beats, lt.total_beats)]),
                            f"measure {mi + 1}: " + "; ".join(reasons)))
    quality = 1 - cost
    info(f"Alignment: grid '{name}', match quality {quality:.2f}, {len(flagged)} measure(s) flagged")
    if quality < 0.45:
        warn("The tab matches the audio poorly overall. Is it the right song/version/track? "
             "Listen to the check mix.")
    return Alignment(times, name, cost, beat_cost, shift, flagged, grid)


# --------------------------------------------------------------------------- tab -> chart

def build_chart_parts(lt: LinearTab, al: Alignment, bm: BeatMap, song_length: float, tuning: list[int],
                      min_sustain_beats: float) -> tuple[list[Beat], list[Note], list[Section], list[TimeSignatureEvent]]:
    times = al.tab_beat_times

    def q_time(q: float) -> float:
        b = float(_q_to_beat(lt, q))
        return float(np.interp(b, np.arange(len(times)), times))

    # Ebeats: tab beats with tab measures; audio beats before/after the tab.
    beats: list[Beat] = []
    # Count-in measures of the tab that would start before the audio begins are dropped.
    keep = [k for k, mb in enumerate(lt.measure_start_beat) if times[mb] >= 0.0]
    if not keep:
        raise RuntimeError("Tab alignment placed the whole tab before the start of the audio.")
    first_kept = keep[0]
    if first_kept:
        info(f"Dropped {first_kept} tab measure(s) of count-in that come before the audio starts.")
    start_beat = lt.measure_start_beat[first_kept]
    tab_ibi0 = times[start_beat + 1] - times[start_beat]
    # Lead-in before the tab: the tab's first beat interval, grouped in its measure length.
    first_m = lt.measures[first_kept]
    t0_tab = times[start_beat]
    lead = []
    t = t0_tab - tab_ibi0
    while t > 0.02:
        lead.append(t)
        t -= tab_ibi0
    lead = lead[::-1]
    mnum = 0
    for k, t in enumerate(lead):
        is_db = (len(lead) - k) % first_m.beats == 0
        if is_db or k == 0:
            mnum += 1
        beats.append(Beat(t, mnum if (is_db or k == 0) else -1))
    tsigs: list[TimeSignatureEvent] = []
    for m, mb in list(zip(lt.measures, lt.measure_start_beat))[first_kept:]:
        mnum += 1
        for k in range(m.beats):
            beats.append(Beat(float(times[mb + k]), mnum if k == 0 else -1))
        if not tsigs or (tsigs[-1].numerator, tsigs[-1].denominator) != (m.numerator, m.denominator):
            tsigs.append(TimeSignatureEvent(float(times[mb]), m.numerator, m.denominator))
    # Where the recording has beats the tab doesn't (skipped by the alignment), insert the
    # recording's beats as an extra measure so the beat grid keeps following the audio.
    if al.grid is not None and len(beats) > 2:
        grid = al.grid
        filled: list[Beat] = []
        for a, b in zip(beats, beats[1:] + [None]):
            filled.append(a)
            if b is None:
                continue
            gap = b.time - a.time
            ibi = float(np.median(np.diff(grid))) if len(grid) > 1 else gap
            inner = grid[(grid > a.time + 0.3 * ibi) & (grid < b.time - 0.3 * ibi)]
            if gap > 1.5 * ibi and len(inner):
                for k, t in enumerate(inner):
                    filled.append(Beat(float(t), 0 if k == 0 else -1))
                if b.measure < 0:
                    b.measure = 0
        beats = filled
        m = 0
        for bt_ in beats:
            if bt_.measure >= 0:
                m += 1
                bt_.measure = m
        mnum = m
    # After the tab: continue with the tab's last measure length until the audio ends.
    last_m = lt.measures[-1]
    ibi = times[-1] - times[-2]
    t = times[-1]
    k = 0
    while t < song_length + ibi:
        if k % last_m.beats == 0:
            mnum += 1
        beats.append(Beat(t, mnum if k % last_m.beats == 0 else -1))
        t += ibi
        k += 1

    # Notes
    notes: list[Note] = []
    for qs, qd, n in sorted(lt.notes, key=lambda x: (x[0], x[2].string)):
        t0 = q_time(qs)
        t1 = q_time(qs + qd)
        if n.tie and notes:
            prev = next((p for p in reversed(notes) if p.string == n.string), None)
            if prev is not None:
                prev.sustain = max(prev.sustain, t1 - prev.time)
                prev._dur_beats += qd  # type: ignore[attr-defined]
                continue
        pitch = tuning[n.string] + n.fret + al.pitch_shift if 0 <= n.string < len(tuning) else 0
        note = Note(time=t0, sustain=t1 - t0, pitch=pitch, string=n.string, fret=n.fret,
                    mute=n.dead, palm_mute=n.palm_mute, harmonic=n.harmonic, accent=n.accent,
                    vibrato=n.vibrato, slap=n.slap, pop=n.pop, tap=n.tap, tremolo=n.tremolo, bend=n.bend)
        note._dur_beats = qd  # type: ignore[attr-defined]
        note._hammer_next = n.hammer_to_next  # type: ignore[attr-defined]
        note._slide = n.slide  # type: ignore[attr-defined]
        notes.append(note)

    # Bass is charted as single notes: if the tab has chords (double stops), keep the lowest.
    by_time: dict[float, Note] = {}
    dropped = 0
    for n in notes:
        k = round(n.time, 3)
        if k in by_time:
            dropped += 1
            if n.pitch < by_time[k].pitch:
                by_time[k] = n
        else:
            by_time[k] = n
    if dropped:
        warn(f"{dropped} double-stop/chord note(s) in the tab were reduced to their lowest note.")
    notes = sorted(by_time.values(), key=lambda n: n.time)

    # Techniques that refer to the next note on the same string; sustains.
    for i, n in enumerate(notes):
        nxt = notes[i + 1] if i + 1 < len(notes) else None
        same = nxt is not None and nxt.string == n.string
        if n._hammer_next and same:  # type: ignore[attr-defined]
            if nxt.fret > n.fret:
                nxt.hammer_on = True
            elif nxt.fret < n.fret:
                nxt.pull_off = True
        slide = n._slide  # type: ignore[attr-defined]
        if slide in ("shift", "legato") and same and nxt.fret != n.fret and nxt.fret > 0:
            n.slide_to = nxt.fret
            if slide == "legato":
                n.link_next = True
        elif slide == "out_down" and n.fret > 1:
            n.slide_unpitch_to = max(1, n.fret - 5)
        elif slide == "out_up":
            n.slide_unpitch_to = min(24, n.fret + 5)
        gap_to_next = (nxt.time - n.time) if nxt else n.sustain
        needs_sustain = n.slide_to >= 0 or n.slide_unpitch_to >= 0 or n.vibrato or n.bend > 0 or n.tremolo
        if n._dur_beats < min_sustain_beats and not needs_sustain:  # type: ignore[attr-defined]
            n.sustain = 0.0
        else:
            n.sustain = max(0.1, min(n.sustain, gap_to_next) - 0.04)
        if n.link_next and nxt is not None:
            n.sustain = max(0.05, nxt.time - n.time)
    # Linked notes must not have their own attack: Rocksmith wants the target to exist (it does).

    sections = []
    for m, mb in zip(lt.measures, lt.measure_start_beat):
        if m.marker:
            sections.append(Section(m.marker, float(times[mb])))
    return beats, notes, sections, tsigs
