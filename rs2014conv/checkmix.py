"""'Check mix' renders: the original audio with a synth bass playing the chart (+ clicks)."""

from __future__ import annotations

import numpy as np

from .model import Chart


def synth_bass(chart: Chart, n_samples: int, sr: int, cents: float = 0.0) -> np.ndarray:
    out = np.zeros(n_samples, np.float32)
    notes = chart.notes
    for i, n in enumerate(notes):
        nxt = notes[i + 1].time if i + 1 < len(notes) else n.time + 1.0
        dur = n.sustain if n.sustain > 0 else min(0.35, max(0.08, nxt - n.time - 0.02))
        if n.mute:
            dur = 0.06
        length = int((dur + 0.06) * sr)
        start = int(n.time * sr)
        if start >= n_samples:
            continue
        length = min(length, n_samples - start)
        t = np.arange(length) / sr
        ref = 440.0 * 2 ** (cents / 1200)  # play in the recording's tuning reference
        f0 = ref * 2 ** ((n.pitch - 69) / 12)
        if n.slide_to >= 0 and n.sustain > 0:
            f1 = ref * 2 ** ((n.pitch + (n.slide_to - n.fret) - 69) / 12)
            freq = f0 + (f1 - f0) * np.clip(t / max(dur, 1e-3), 0, 1)
            phase = 2 * np.pi * np.cumsum(freq) / sr
        else:
            phase = 2 * np.pi * f0 * t
        # A few harmonics with decreasing level: reads clearly even on small speakers.
        wave = (np.sin(phase) + 0.5 * np.sin(2 * phase) + 0.25 * np.sin(3 * phase) + 0.12 * np.sin(4 * phase))
        if n.mute:
            wave = np.random.default_rng(i).standard_normal(length) * 0.3
        env = np.minimum(1.0, t / 0.004) * np.exp(-t * 1.5)
        rel = np.clip((dur + 0.06 - t) / 0.06, 0, 1)
        out[start:start + length] += (0.35 * wave * env * rel).astype(np.float32)
    return out


def clicks(chart: Chart, n_samples: int, sr: int) -> np.ndarray:
    out = np.zeros(n_samples, np.float32)
    length = int(0.03 * sr)
    t = np.arange(length) / sr
    for b in chart.beats:
        s = int(b.time * sr)
        if s < 0 or s >= n_samples:
            continue
        f = 1800.0 if b.measure >= 0 else 1200.0
        burst = np.sin(2 * np.pi * f * t) * np.exp(-t * 120) * (0.5 if b.measure >= 0 else 0.3)
        e = min(n_samples, s + length)
        out[s:e] += burst[: e - s].astype(np.float32)
    return out


def render(chart: Chart, mix: np.ndarray, stem: np.ndarray | None, sr: int, with_clicks: bool,
           synth_gain_db: float = 0.0, cents: float = 0.0) -> tuple[np.ndarray, np.ndarray | None]:
    """Returns (check mix stereo, split mix stereo [stem left / synth right] or None)."""
    n = mix.shape[1]
    syn = synth_bass(chart, n, sr, cents) * (10 ** (synth_gain_db / 20))
    clk = clicks(chart, n, sr) if with_clicks else 0.0
    main = mix * 0.6 + (syn + clk)[None] * 0.8
    peak = np.abs(main).max()
    if peak > 0.99:
        main = main / peak * 0.99
    split = None
    if stem is not None:
        m = stem.mean(0)[:n]
        if len(m) < n:
            m = np.pad(m, (0, n - len(m)))
        m = m / (np.abs(m).max() + 1e-9) * 0.7
        s = syn + (clk if with_clicks else 0.0)
        s = s / (np.abs(s).max() + 1e-9) * 0.7
        split = np.vstack([m, s])
    return main.astype(np.float32), split
