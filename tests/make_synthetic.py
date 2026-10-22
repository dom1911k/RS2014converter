"""Create a synthetic test song with known bass notes, a drifting tempo and a matching GP5 tab.

  python tests/make_synthetic.py OUTDIR [--tuning-shift -1]

Writes OUTDIR/mix.wav, OUTDIR/bass.wav (the isolated bass), OUTDIR/tab.gp5 and
OUTDIR/truth.json (note times/pitches) for the accuracy tests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100

# Bass line per bar (4/4), as (beat position, length in beats, string, fret) in E standard.
RIFF_A = [(0, .5, 0, 0), (.5, .5, 0, 0), (1, .5, 0, 3), (1.5, .5, 0, 5), (2, 1, 1, 2), (3, .5, 0, 5), (3.5, .5, 0, 3)]
RIFF_B = [(0, 1, 1, 0), (1, .5, 1, 0), (1.5, .5, 2, 2), (2, .5, 1, 3), (2.5, .5, 1, 2), (3, 1, 0, 3)]
RIFF_C = [(0, 2, 2, 0), (2, 1, 1, 5), (3, .5, 1, 3), (3.5, .5, 1, 2)]
RIFF_D = [(0, .25, 0, 0), (.25, .25, 0, 0), (.5, .5, 1, 2), (1, .5, 0, 0), (1.5, .5, 2, 2),
          (2, .5, 0, 0), (2.5, .5, 3, 2), (3, 1, 2, 2)]
TUNING = [28, 33, 38, 43]
FORM = ["A", "A", "B", "B", "C", "D", "A", "A"]  # verse x2, B x2, C, D, verse x2 (tab uses repeats)
RIFFS = {"A": RIFF_A, "B": RIFF_B, "C": RIFF_C, "D": RIFF_D}


def tempo_beat_times(n_beats: int, start: float, bpm0: float, bpm1: float) -> np.ndarray:
    bpm = np.linspace(bpm0, bpm1, n_beats)
    ibi = 60.0 / bpm
    return start + np.concatenate([[0], np.cumsum(ibi[:-1])])


def bass_tone(f0: float, dur: float, seed: int, fundamental: float = 0.55) -> np.ndarray:
    n = int((dur + 0.08) * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    # Weak fundamental (like a real bass in a mix) + strong 2nd harmonic: provokes octave errors.
    amps = [fundamental, 1.0, 0.6, 0.35, 0.2, 0.12]
    y = sum(a * np.sin(2 * np.pi * f0 * (k + 1) * t + rng.uniform(0, 6.28)) * np.exp(-t * (1.2 + k))
            for k, a in enumerate(amps))
    env = np.minimum(1, t / 0.005) * np.clip((dur + 0.08 - t) / 0.08, 0, 1)
    return (y * env * 0.25).astype(np.float32)


def drum_hit(kind: str, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if kind == "kick":
        n = int(0.25 * SR)
        t = np.arange(n) / SR
        f = 120 * np.exp(-t * 25) + 45
        return (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 14) * 0.9).astype(np.float32)
    if kind == "snare":
        n = int(0.2 * SR)
        t = np.arange(n) / SR
        return ((rng.standard_normal(n) * 0.5 + np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 22) * 0.5).astype(np.float32)
    n = int(0.05 * SR)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    noise = np.diff(np.concatenate([[0], noise]))
    return (noise * np.exp(-t * 90) * 0.15).astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--tuning-shift", type=int, default=0, help="render the audio this many semitones off the tab")
    ap.add_argument("--lead", type=float, default=1.3)
    ap.add_argument("--form", default="".join(FORM), help="riff letters actually played in the audio")
    ap.add_argument("--fundamental", type=float, default=0.55, help="level of the fundamental vs 2nd harmonic")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    form = list(a.form)
    bars = len(form) + 2  # + 1 bar drums-only intro + 1 bar ending
    n_beats = bars * 4 + 1
    bt = tempo_beat_times(n_beats, a.lead, 112, 124)  # drifting tempo
    length = bt[-1] + 2.0
    mix = np.zeros(int(length * SR), np.float32)
    bass = np.zeros_like(mix)

    def add(buf, s, sig):
        i = int(s * SR)
        e = min(len(buf), i + len(sig))
        buf[i:e] += sig[: e - i]

    def time_of(beat: float) -> float:
        return float(np.interp(beat, np.arange(len(bt)), bt))

    truth = []
    seed = 0
    for bar_i, sec in enumerate(form):
        bar = bar_i + 1
        for pos, ln, s, f in RIFFS[sec]:
            b0 = bar * 4 + pos
            t0, t1 = time_of(b0), time_of(b0 + ln)
            pitch = TUNING[s] + f + a.tuning_shift
            f0 = 440 * 2 ** ((pitch - 69) / 12)
            add(bass, t0, bass_tone(f0, t1 - t0 - 0.02, seed, a.fundamental))
            truth.append({"time": t0, "end": t1, "pitch": pitch, "string": s, "fret": f})
            seed += 1
    # Drums for all bars, chords pad (simple triads) in the bass-line bars
    for b in range(bars * 4):
        t = bt[b]
        add(mix, t, drum_hit("kick" if b % 2 == 0 else "snare", b))
        add(mix, t, drum_hit("hat", 1000 + b))
        add(mix, (bt[b] + bt[b + 1]) / 2, drum_hit("hat", 2000 + b))
    for bar_i, sec in enumerate(form):
        root = {"A": 52, "B": 57, "C": 62, "D": 52}[sec] + a.tuning_shift
        t0, t1 = time_of((bar_i + 1) * 4), time_of((bar_i + 2) * 4)
        n = int((t1 - t0) * SR)
        tt = np.arange(n) / SR
        pad = sum(np.sin(2 * np.pi * 440 * 2 ** ((root + iv + 12 - 69) / 12) * tt) for iv in (0, 4, 7)) * 0.04
        add(mix, t0, (pad * np.minimum(1, tt / 0.05)).astype(np.float32))
    mix = mix + bass
    peak = np.abs(mix).max()
    mix, bass = mix / peak * 0.9, bass / peak * 0.9
    sf.write(out / "mix.wav", np.stack([mix, mix], 1), SR)
    sf.write(out / "bass.wav", np.stack([bass, bass], 1), SR)
    (out / "truth.json").write_text(json.dumps({"notes": truth, "beats": bt.tolist()}, indent=1))
    write_tab(out / "tab.gp5")
    print(f"wrote {out}")


def write_tab(path: Path) -> None:
    """GP5 with a count-in bar, the verse as a repeat, markers and a constant 120 BPM (not the real tempo)."""
    import guitarpro as gp

    song = gp.Song()
    song.title, song.artist, song.tempo = "Synthetic", "Test Band", 120
    song.tracks = []
    song.measureHeaders = []
    guitar = gp.Track(song, number=1, name="Guitar", strings=[gp.GuitarString(i + 1, v) for i, v in enumerate([64, 59, 55, 50, 45, 40])])
    bass = gp.Track(song, number=2, name="Bass", strings=[gp.GuitarString(i + 1, v) for i, v in enumerate([43, 38, 33, 28])])
    bass.channel.instrument = 33
    song.tracks = [guitar, bass]
    # Written form: count-in, |: A :|, |: B :|, C, D, |: A :|
    written = [(None, "", False, 0), ("A", "Verse", True, 1), ("B", "Chorus", True, 1), ("C", "Bridge", False, 0),
               ("D", "Riff", False, 0), ("A", "Verse", True, 1)]
    start = gp.Duration.quarterTime
    for i, (riff, marker, rep, close) in enumerate(written):
        h = gp.MeasureHeader(number=i + 1, start=start)
        h.isRepeatOpen = rep
        h.repeatClose = close if rep else -1
        if marker:
            h.marker = gp.Marker(title=marker)
        song.measureHeaders.append(h)
        for tr in (guitar, bass):
            m = gp.Measure(tr, h)
            tr.measures.append(m)
            v = m.voices[0]
            if tr is guitar or riff is None:
                b = gp.Beat(v, status=gp.BeatStatus.rest)
                b.duration = gp.Duration(value=1)
                v.beats.append(b)
                continue
            for pos, ln, s, f in RIFFS[riff]:
                b = gp.Beat(v, status=gp.BeatStatus.normal)
                b.duration = gp.Duration(value=int(round(4 / ln)))
                b.notes.append(gp.Note(b, value=f, string=4 - s, type=gp.NoteType.normal))
                v.beats.append(b)
        start += 4 * gp.Duration.quarterTime
    gp.write(song, str(path))


if __name__ == "__main__":
    main()
