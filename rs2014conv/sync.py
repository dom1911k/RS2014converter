"""Final timing check: line the chart up with the attacks of the real bass.

The pluck of a bass string makes a short bright click (above ~1 kHz) exactly at the attack; the
low end swells in later, so it is a poor ruler. If the chart's notes are consistently early or late
against those clicks, the whole chart (beats and notes together) is shifted.
"""

from __future__ import annotations

import numpy as np

from . import audio_io
from .model import Chart


def attack_times(stem_path, sr: int = 22050, hop: int = 64) -> np.ndarray:
    import librosa

    y = audio_io.load(stem_path, sr=sr, mono=True)[0]
    if float(np.abs(y).max()) < 1e-4:
        return np.array([])
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=1024)
    # n_fft must be passed too: librosa uses it to undo the frame-centering delay (else ~25 ms late).
    env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[freqs >= 1000]), sr=sr, hop_length=hop,
                                       n_fft=1024, lag=1, max_size=1)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, backtrack=False,
                                        units="frames", delta=0.1)
    return frames * hop / sr


def measure_offset(note_times: np.ndarray, attacks: np.ndarray, window: float = 0.08) -> tuple[float, float, float]:
    """(median offset of the attacks relative to the notes, share of notes with an attack, spread)."""
    if not len(note_times) or not len(attacks):
        return 0.0, 0.0, 1.0
    idx = np.clip(np.searchsorted(attacks, note_times), 1, len(attacks) - 1)
    nearest = np.where(np.abs(attacks[idx] - note_times) < np.abs(attacks[idx - 1] - note_times),
                       attacks[idx], attacks[idx - 1])
    d = nearest - note_times
    near = np.abs(d) < window
    if near.sum() < 10:
        return 0.0, float(near.mean()), 1.0
    q25, q50, q75 = np.percentile(d[near], [25, 50, 75])
    return float(q50), float(near.mean()), float(q75 - q25)


def shift_chart(chart: Chart, shift: float) -> None:
    for b in chart.beats:
        b.time += shift
    for n in chart.notes:
        n.time += shift
    for s in chart.sections:
        s.time += shift
    for ts in chart.time_signatures:
        ts.time += shift
    chart.flagged = [(a + shift, b + shift, r) for a, b, r in chart.flagged]
    chart.beats = [b for b in chart.beats if b.time > 0.01]
    if chart.beats and chart.beats[0].measure < 0:
        chart.beats[0].measure = 0
        m = 0
        for b in chart.beats:
            if b.measure >= 0:
                m += 1
                b.measure = m
