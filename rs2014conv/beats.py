"""Beat and downbeat tracking, and the BeatMap used by every later stage.

The beat map is never assumed to have a constant tempo: every beat has its own time, and
positions between beats are interpolated linearly (so tempo drift is followed exactly).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import audio_io
from .log import info, warn


@dataclass
class BeatMap:
    times: np.ndarray  # beat times in seconds, strictly increasing
    downbeat: np.ndarray  # bool per beat
    source: str = ""

    # ---- conversions between seconds and (fractional) beat index ----
    def beat_at(self, t: float | np.ndarray) -> np.ndarray:
        """Fractional beat index for time t (extrapolates with the edge tempo)."""
        bt = self.times
        idx = np.arange(len(bt), dtype=float)
        t = np.asarray(t, dtype=float)
        out = np.interp(t, bt, idx)
        first_ibi = bt[1] - bt[0]
        last_ibi = bt[-1] - bt[-2]
        out = np.where(t < bt[0], (t - bt[0]) / first_ibi, out)
        out = np.where(t > bt[-1], len(bt) - 1 + (t - bt[-1]) / last_ibi, out)
        return out

    def time_at(self, b: float | np.ndarray) -> np.ndarray:
        bt = self.times
        idx = np.arange(len(bt), dtype=float)
        b = np.asarray(b, dtype=float)
        out = np.interp(b, idx, bt)
        first_ibi = bt[1] - bt[0]
        last_ibi = bt[-1] - bt[-2]
        out = np.where(b < 0, bt[0] + b * first_ibi, out)
        out = np.where(b > len(bt) - 1, bt[-1] + (b - (len(bt) - 1)) * last_ibi, out)
        return out

    def tempo_curve(self) -> np.ndarray:
        return 60.0 / np.diff(self.times)

    def average_tempo(self) -> float:
        return float(60.0 / np.median(np.diff(self.times)))

    def measure_numbers(self) -> list[int]:
        """Measure number for each beat that starts a measure, -1 otherwise (Rocksmith ebeats)."""
        out, m = [], 0
        for d in self.downbeat:
            if d:
                m += 1
                out.append(m)
            else:
                out.append(-1)
        return out

    def beats_per_measure(self) -> list[tuple[int, int]]:
        """(beat index of downbeat, beats in that measure)."""
        dbs = list(np.flatnonzero(self.downbeat)) + [len(self.times)]
        return [(dbs[i], dbs[i + 1] - dbs[i]) for i in range(len(dbs) - 1)]

    def to_json(self) -> dict:
        return {"times": self.times.tolist(), "downbeat": self.downbeat.astype(int).tolist(),
                "source": self.source}

    @staticmethod
    def from_json(d: dict) -> "BeatMap":
        return BeatMap(np.array(d["times"], float), np.array(d["downbeat"], bool), d.get("source", ""))


# --------------------------------------------------------------------------- tracking

def _track_beat_this(mix_path: Path, device: str, checkpoint: str = "final0") -> tuple[np.ndarray, np.ndarray]:
    import torch
    from beat_this.inference import Audio2Beats

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    info(f"Tracking beats with Beat This! on {device}...")
    tracker = Audio2Beats(checkpoint_path=checkpoint, device=device, dbn=False)
    sr = 22050
    signal = audio_io.load(mix_path, sr=sr, mono=True)[0]  # our own decoder: works for mp3/flac/...
    beats, downbeats = tracker(signal, sr)
    return np.asarray(beats, float), np.asarray(downbeats, float)


def _track_librosa(mix_path: Path, beats_per_bar: int = 4) -> tuple[np.ndarray, np.ndarray]:
    import librosa

    info("Tracking beats with librosa (fallback tracker)...")
    sr = 22050
    y = audio_io.load(mix_path, sr=sr, mono=True)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256, aggregate=np.median)
    _, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=256,
                                             tightness=60, trim=False)
    beats = librosa.frames_to_time(beat_frames, sr=sr, hop_length=256)
    # Downbeats: pick the bar phase where low-frequency onsets (kick/bass) are strongest.
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=256))
    low = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[:40]), sr=sr, hop_length=256)
    vals = low[np.clip(beat_frames, 0, len(low) - 1)]
    scores = [vals[p::beats_per_bar].mean() if len(vals[p::beats_per_bar]) else 0 for p in range(beats_per_bar)]
    phase = int(np.argmax(scores))
    return beats, beats[phase::beats_per_bar]


def _refine(beats: np.ndarray, mix_path: Path) -> np.ndarray:
    """Remove a constant latency of the tracker by comparing beats with sharp onsets in the mix."""
    import librosa

    sr, hop = 22050, 64
    y = audio_io.load(mix_path, sr=sr, mono=True)[0]
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, lag=1, max_size=1, n_mels=96)
    times = librosa.frames_to_time(np.arange(len(env)), sr=sr, hop_length=hop)
    w = int(0.06 * sr / hop)
    deltas, weights = [], []
    for b in beats:
        i = int(round(b * sr / hop))
        lo, hi = max(0, i - w), min(len(env), i + w + 1)
        if hi - lo < 3:
            continue
        k = lo + int(np.argmax(env[lo:hi]))
        # Onset start: walk back from the peak to where the rise begins (half height).
        thr = env[k] * 0.5
        while k > lo and env[k - 1] > thr:
            k -= 1
        deltas.append(times[k] - b)
        weights.append(env[lo:hi].max())
    if len(deltas) < 8:
        return beats
    deltas, weights = np.array(deltas), np.array(weights)
    strong = weights >= np.percentile(weights, 40)
    shift = float(np.median(deltas[strong]))
    if abs(shift) > 0.006:
        info(f"Beat timing corrected by {shift * 1000:+.0f} ms (tracker latency)")
        return beats + shift
    return beats


def _clean(beats: np.ndarray, downbeats: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Remove duplicate beats, fill single missed beats, and mark downbeats on the beat grid."""
    beats = np.unique(np.round(beats, 4))
    if len(beats) < 4:
        raise RuntimeError("Too few beats detected - is the audio file silent or very short?")
    ibi = np.diff(beats)
    med = np.median(ibi)
    keep = np.concatenate([[True], ibi > 0.4 * med])
    beats = beats[keep]

    # Fill gaps of ~2x/3x the local beat interval (missed beats).
    out = [beats[0]]
    for a, b in zip(beats[:-1], beats[1:]):
        lo = max(0, len(out) - 9)
        local = float(np.median(np.diff(out[lo:]))) if len(out) > 2 else med
        n = int(round((b - a) / local))
        if n >= 2 and abs((b - a) / n - local) < 0.15 * local:
            out.extend(a + (b - a) * k / n for k in range(1, n))
        out.append(b)
    beats = np.array(out)

    db = np.zeros(len(beats), bool)
    for d in downbeats:
        i = int(np.argmin(np.abs(beats - d)))
        if abs(beats[i] - d) < 0.25 * med:
            db[i] = True
    if not db.any():
        db[::4] = True
    # Beats before the first downbeat belong to a pickup measure; make sure beat 0 starts one.
    db[0] = True
    return beats, db


def _extend(beats: np.ndarray, db: np.ndarray, song_length: float) -> tuple[np.ndarray, np.ndarray]:
    """Extend the grid back towards 0 s and forward past the end of the audio.

    Rocksmith needs beats covering the whole song. Extra beats before the music use the
    tempo of the first detected beats, grouped in measures of the first measure's length.
    """
    first_ibi = float(np.median(np.diff(beats[:5])))
    last_ibi = float(np.median(np.diff(beats[-5:])))
    first_dbs = np.flatnonzero(db)
    bpm = int(first_dbs[1] - first_dbs[0]) if len(first_dbs) > 1 and first_dbs[1] - first_dbs[0] <= 7 else 4

    pre = []
    t = beats[0] - first_ibi
    while t > 0.02:
        pre.append(t)
        t -= first_ibi
    pre = pre[::-1]
    post = []
    t = beats[-1] + last_ibi
    while t < song_length + last_ibi:
        post.append(t)
        t += last_ibi

    times = np.concatenate([pre, beats, post])
    flags = np.concatenate([np.zeros(len(pre), bool), db, np.zeros(len(post), bool)])
    # Downbeats for the prepended beats: count back from the first real downbeat.
    first_real = len(pre) + (first_dbs[0] if len(first_dbs) else 0)
    for i in range(first_real - bpm, -1, -bpm):
        flags[i] = True
    # Continue the bar pattern into the appended beats.
    last_dbs = np.flatnonzero(db)
    if len(last_dbs) >= 2:
        bar = int(last_dbs[-1] - last_dbs[-2])
        start = len(pre) + last_dbs[-1]
        for i in range(start + bar, len(times), bar):
            flags[i] = True
    flags[0] = True
    return times, flags


def detect(mix_path: Path, cache_dir: Path, tracker: str = "beat_this", device: str = "auto",
           song_length: float | None = None, checkpoint: str = "final0") -> BeatMap:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = audio_io.file_hash(mix_path, f"beats:{tracker}:v2")
    cache = cache_dir / f"beats_{key}.json"
    if cache.exists():
        info(f"Beat map: using cached result ({cache.name})")
        return BeatMap.from_json(json.loads(cache.read_text()))

    if song_length is None:
        song_length = audio_io.duration(mix_path)
    source = tracker
    if tracker == "beat_this":
        try:
            beats, downbeats = _track_beat_this(mix_path, device, checkpoint)
        except Exception as e:  # noqa: BLE001 - any failure falls back
            warn(f"Beat This! failed ({e.__class__.__name__}: {e}); using the librosa tracker instead. "
                 "Check the beat map in the check mix.")
            beats, downbeats = _track_librosa(mix_path)
            source = "librosa"
    else:
        beats, downbeats = _track_librosa(mix_path)

    beats, db = _clean(beats, downbeats)
    shifted = _refine(beats, mix_path)
    beats = shifted
    times, flags = _extend(beats, db, song_length)
    bm = BeatMap(times, flags, source)
    if source == tracker:  # don't cache a fallback result: retry the real tracker next time
        cache.write_text(json.dumps(bm.to_json()))
    tempo = bm.tempo_curve()
    info(f"{len(beats)} beats detected ({source}); tempo {np.percentile(tempo, 5):.1f}-"
         f"{np.percentile(tempo, 95):.1f} BPM, median {bm.average_tempo():.1f}")
    return bm


def first_sound_time(mix: np.ndarray, sr: int, threshold_db: float = -45.0) -> float:
    """Time where the audio first gets louder than threshold (relative to its peak)."""
    mono = mix.mean(0) if mix.ndim == 2 else mix
    hop = sr // 100
    n = len(mono) // hop
    if n == 0:
        return 0.0
    frames = mono[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms / (rms.max() + 1e-12))
    idx = np.flatnonzero(db > threshold_db)
    return float(idx[0] * hop / sr) if len(idx) else 0.0
