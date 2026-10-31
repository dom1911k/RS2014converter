"""Songsterr timing: use the times in a Bass Trainer chart instead of aligning beat by beat.

A Bass Trainer chart (.btchart.json, made from a Songsterr tab) knows when every bar starts in
Songsterr's own recording. If your audio file is that recording, the chart only has to be
shifted by a constant offset (your file may start earlier or later), and sometimes stretched a
tiny bit (a different sample rate or speed). Both are found by matching the chart's notes to the
attacks of the real bass (sync.attack_times on the bass stem):

  offset  every note's (nearest attack - note time) goes into a histogram; its peak is the offset.
          With an offset from song.yaml (`chart_offset`, set in the Bass Trainer player) only a
          small window around it is searched.
  drift   a straight line through the matched differences over time (only used if it moves the
          end of the song by more than 15 ms).

If enough notes land on attacks (tab.chart_timing_min_match, default 60 %), the chart's times are
used; otherwise the recording is a different version and the usual beat alignment runs.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .log import info

MATCH_S = 0.045  # a note "lands on" an attack within this distance


@dataclass
class TimingFit:
    offset: float  # audio time of chart time 0
    drift: float  # extra seconds per chart second (audio = chart + offset + drift * chart)
    match: float  # share of the notes that land on an attack
    matched: int
    total: int
    pitch: float | None = None  # how strongly the recording has the chart's notes there (0-1)
    runner_up: float | None = None  # offset of the next best candidate (if close: ambiguous)

    def to_audio(self, t):
        return np.asarray(t, dtype=float) * (1.0 + self.drift) + self.offset


def _nearest(attacks: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Signed distance from each time in t to the nearest attack."""
    i = np.clip(np.searchsorted(attacks, t), 1, len(attacks) - 1)
    left, right = attacks[i - 1], attacks[i]
    return np.where(np.abs(left - t) <= np.abs(right - t), left - t, right - t)


def fit_timing(note_times: np.ndarray, attacks: np.ndarray, center: float | None = None,
               window: float = 60.0, pitch_score=None) -> TimingFit:
    """Offset (and drift) that put the chart's notes on the recording's attacks. Pure function.

    pitch_score(fit) -> 0..1 (optional): how well the recording's pitches match the chart with
    this timing. It decides between candidates that fit the attacks about equally well (a riff
    repeated bar after bar matches one or more bars off too).
    """
    notes = np.sort(np.asarray(note_times, dtype=float))
    attacks = np.sort(np.asarray(attacks, dtype=float))
    if len(notes) < 4 or len(attacks) < 4:
        return TimingFit(center or 0.0, 0.0, 0.0, 0, len(notes))
    lo, hi = (center - 0.15, center + 0.15) if center is not None else (-window, window)
    # Histogram of (attack - note) over all pairs within the search range (10 ms bins).
    diffs = []
    for t in notes:
        a, b = np.searchsorted(attacks, [t + lo, t + hi])
        diffs.append(attacks[a:b] - t)
    d = np.concatenate(diffs) if diffs else np.array([])
    if not len(d):
        return TimingFit(center or 0.0, 0.0, 0.0, 0, len(notes))
    bins = np.arange(lo, hi + 0.01, 0.01)

    def peaks_of(diffs: np.ndarray, n: int) -> list[float]:
        hist, edges = np.histogram(diffs, bins=bins)
        smooth = np.convolve(hist, [1, 1, 1], mode="same")  # the offset can fall on a bin edge
        ks = [k for k in range(len(smooth)) if smooth[k] > 0
              and smooth[k] >= smooth[max(0, k - 1)] and smooth[k] >= smooth[min(len(smooth) - 1, k + 1)]]
        ks = [k for k in ks if smooth[k] >= 0.4 * smooth.max()]   # every strong peak (cheap to test)
        return [float((edges[k] + edges[k + 1]) / 2) for k in sorted(ks, key=lambda k: -smooth[k])[:n]]

    # Repetitive songs also match well one or more bars off, and a slight speed difference
    # smears the true peak over a long song: candidates come from the whole song and from its
    # first quarter (little drift there). Each is refined, drift fitted, and the one where the
    # most notes land on an attack wins (pitch decides between near-equal ones).
    head = notes[: max(8, len(notes) // 4)]
    head_d = np.concatenate([attacks[slice(*np.searchsorted(attacks, [t + lo, t + hi]))] - t for t in head])
    cands = peaks_of(d, 400) + (peaks_of(head_d, 400) if len(head_d) else [])
    cands = sorted(set(round(c, 2) for c in cands))
    fits = [_fit_at(notes, attacks, d, c) for c in cands]
    top = max(f.match for f in fits)
    close = [f for f in fits if f.match >= top - 0.05]
    if pitch_score is not None:
        for f in close:
            f.pitch = float(pitch_score(f))
        close.sort(key=lambda f: -(f.match + f.pitch))
    else:
        close.sort(key=lambda f: (-f.match, abs(f.drift)))
    best = close[0]
    others = [f for f in close[1:] if abs(f.offset - best.offset) > 0.2]
    if others:
        best.runner_up = others[0].offset
    return best


def _fit_at(notes: np.ndarray, attacks: np.ndarray, d: np.ndarray, coarse: float) -> TimingFit:
    near = d[np.abs(d - coarse) < 0.03]
    offset = float(np.median(near)) if len(near) else coarse
    # Drift: a line through the matched differences over time.
    rel = _nearest(attacks, notes + offset)
    ok = np.abs(rel) < MATCH_S
    drift = 0.0
    if ok.sum() >= 20 and np.ptp(notes[ok]) > 30:
        slope, icpt = np.polyfit(notes[ok], rel[ok], 1)
        if abs(slope * np.ptp(notes)) > 0.015:
            drift, offset = float(slope), float(offset + icpt)
    fit = TimingFit(offset, drift, 0.0, 0, len(notes))
    rel = _nearest(attacks, fit.to_audio(notes))
    fit.matched = int((np.abs(rel) < MATCH_S).sum())
    fit.match = fit.matched / len(notes)
    return fit


def chart_beat_times(lt) -> np.ndarray | None:
    """Chart seconds of every tab beat (+ end sentinel), or None if the tab has no chart times."""
    if not lt.measures or any(getattr(m, "seconds", None) is None for m in lt.measures):
        return None
    times = []
    for m in lt.measures:
        t0, t1 = m.seconds
        times += [t0 + (t1 - t0) * k / m.beats for k in range(m.beats)]
    times.append(lt.measures[-1].seconds[1])
    return np.array(times, dtype=float)


def attacked_note_times(lt, beat_times: np.ndarray) -> np.ndarray:
    """Chart seconds of the notes that are plucked (not the second note of a hammer-on,
    pull-off or legato slide, not ghost notes)."""
    from .tab_align import _q_to_beat

    out, prev = [], {}
    for q, _, n in sorted(lt.notes, key=lambda x: (x[0], x[2].string)):
        p = prev.get(n.string)
        legato = p is not None and (p.hammer_to_next or p.slide == "legato")
        prev[n.string] = n
        if legato or n.tie or n.ghost:
            continue
        b = float(_q_to_beat(lt, q))
        out.append(float(np.interp(b, np.arange(len(beat_times)), beat_times)))
    return np.array(out)


def _pitch_scorer(lt, beat_times: np.ndarray, tuning: list[int], stem_path):
    """fit -> mean strength of each note's pitch class in the recording just after the note."""
    from .tab_align import StemFeatures, _q_to_beat

    feats = StemFeatures(stem_path)
    chroma = feats.chroma / (feats.chroma.max(0, keepdims=True) + 1e-9)
    ts, pcs = [], []
    for q, _, n in lt.notes:
        if n.dead or n.ghost or not 0 <= n.string < len(tuning):
            continue
        ts.append(float(np.interp(float(_q_to_beat(lt, q)), np.arange(len(beat_times)), beat_times)))
        pcs.append((tuning[n.string] + n.fret) % 12)
    ts, pcs = np.array(ts), np.array(pcs)

    def score(fit: TimingFit) -> float:
        if not len(ts):
            return 0.0
        fr = feats.frame(fit.to_audio(ts) + 0.06)
        return float(np.mean(chroma[pcs, fr]))
    return score


def align_from_chart(lt, stem_path, center: float | None, min_match: float, tuning: list[int] | None = None):
    """Alignment from the chart's own times, or None if they don't fit the recording."""
    from .sync import attack_times
    from .tab_align import Alignment

    beat_times = chart_beat_times(lt)
    if beat_times is None:
        return None, None
    notes = attacked_note_times(lt, beat_times)
    attacks = np.sort(attack_times(stem_path))
    scorer = _pitch_scorer(lt, beat_times, tuning, stem_path) if tuning else None
    fit = fit_timing(notes, attacks, center=center, pitch_score=scorer)
    if center is not None and fit.match < min_match:
        # The offset from the player may be off by more than the small window: search everywhere.
        wide = fit_timing(notes, attacks, pitch_score=scorer)
        if wide.match > fit.match:
            fit = wide
    info(f"Songsterr timing: offset {fit.offset:+.3f} s, drift {fit.drift * 60e3:+.1f} ms/min, "
         f"{fit.match:.0%} of {fit.total} plucked notes on an attack"
         + (f", pitch match {fit.pitch:.2f}" if fit.pitch is not None else "")
         + (f" (next best offset {fit.runner_up:+.3f} s)" if fit.runner_up is not None else ""))
    if fit.match < min_match:
        return None, fit
    times = fit.to_audio(beat_times)
    # Measures where the notes mostly miss the recording's attacks: worth a listen.
    flagged = []
    from .tab_align import _q_to_beat

    for mi, (m, mb) in enumerate(zip(lt.measures, lt.measure_start_beat)):
        qs = [q for q, _, n in lt.notes if mb <= float(_q_to_beat(lt, q)) < mb + m.beats and not n.ghost]
        if len(qs) < 4:
            continue
        ts = fit.to_audio(np.interp([float(_q_to_beat(lt, q)) for q in qs], np.arange(len(beat_times)), beat_times))
        hit = float(np.mean(np.abs(_nearest(attacks, ts)) < MATCH_S))
        if hit < 0.3:
            flagged.append((float(times[mb]), float(times[min(mb + m.beats, len(times) - 1)]),
                            f"measure {mi + 1}: few tab notes land on the recording's attacks ({hit:.0%})"))
    al = Alignment(times, f"Songsterr timing (offset {fit.offset:+.3f} s)", 1.0 - fit.match,
                   np.full(lt.total_beats, np.nan), 0, flagged, None)
    return al, fit
