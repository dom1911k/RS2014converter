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


def _track_librosa(mix_path: Path, stem_path: Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Fallback tracker: dynamic-programming beat tracking that follows a local tempo curve, and
    downbeats chosen where chords / bass notes change and the kick and bass hit."""
    import librosa
    from scipy.ndimage import median_filter

    info("Tracking beats with librosa (fallback tracker)...")
    sr, hop = 22050, 256
    y = audio_io.load(mix_path, sr=sr, mono=True)[0]
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    glob = float(librosa.feature.tempo(onset_envelope=env, sr=sr, hop_length=hop)[0])
    local = librosa.feature.tempo(onset_envelope=env, sr=sr, hop_length=hop, aggregate=None,
                                  ac_size=6.0, std_bpm=2.0, start_bpm=glob)
    # Local estimates jump between tempo octaves (x1/2, x2); a real tempo change is much smaller.
    local = _smooth_tempo_octaves(local)
    local = median_filter(local, size=int(4.0 * sr / hop), mode="nearest")
    _, frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=hop, bpm=local,
                                        tightness=100, trim=False)
    beats = librosa.frames_to_time(frames, sr=sr, hop_length=hop)
    if len(beats) < 8:
        return beats, beats[::4]
    beats = _repair_glitches(beats)
    return beats, _downbeats_from_features(beats, y, sr, stem_path)


def _repair_glitches(beats: np.ndarray, window: int = 16, tol: float = 0.12, max_run: int = 10) -> np.ndarray:
    """Short runs of beats whose spacing jumps away from the local tempo (the tracker briefly locks
    onto a syncopation, e.g. 3/4 of the real beat) are replaced by evenly spaced beats at the local
    tempo. A lasting tempo change is left alone: the local tempo follows it."""
    beats = np.asarray(beats, float)
    ibi = np.diff(beats)
    n = len(ibi)
    local = np.array([np.median(ibi[max(0, i - window // 2):min(n, i + window // 2 + 1)]) for i in range(n)])
    bad = np.abs(ibi - local) > tol * local
    out: list[float] = [beats[0]]
    i = 0
    fixed = 0
    while i < n:
        if not bad[i]:
            out.append(beats[i + 1])
            i += 1
            continue
        j = i
        while j < n and bad[j]:
            j += 1
        a, b = beats[i], beats[j] if j < len(beats) else beats[-1]
        before, after = ibi[max(0, i - window // 2):i], ibi[j:j + window // 2]
        # Only a glitch if the tempo is the same before and after it (else it's a real change).
        same_tempo = (len(before) >= 3 and len(after) >= 3
                      and abs(np.median(before) - np.median(after)) < 0.06 * np.median(before))
        ref = float(np.median(np.concatenate([before, after]))) if same_tempo else 0.0
        k = int(round((b - a) / ref)) if ref > 0 else 0
        if same_tempo and j - i <= max_run and k >= 1 and abs((b - a) / k - ref) < 0.08 * ref:
            out.extend(a + (b - a) * m / k for m in range(1, k + 1))
            fixed += 1
        else:
            out.extend(beats[i + 1:j + 1])
        i = j
    if fixed:
        info(f"Repaired {fixed} short glitch(es) in the beat grid")
    return np.array(out)


def _smooth_tempo_octaves(local: np.ndarray, jump_cost: float = 4.0, prior_cost: float = 0.3) -> np.ndarray:
    """Per frame choose tempo x0.5 / x1 / x2 so the curve is continuous (Viterbi), with a mild
    preference for 70-180 BPM."""
    factors = np.array([0.5, 1.0, 2.0])
    cand = np.log2(np.maximum(local, 1.0)[:, None] * factors[None])  # (n, 3)
    prior = prior_cost * np.maximum(0, np.abs(cand - np.log2(115.0)) - 0.65)
    n = len(local)
    cost = prior[0].copy()
    back = np.zeros((n, 3), int)
    for k in range(1, n):
        trans = cost[None, :] + jump_cost * np.abs(cand[k][:, None] - cand[k - 1][None, :])
        back[k] = trans.argmin(1)
        cost = trans.min(1) + prior[k]
    j = int(cost.argmin())
    out = np.empty(n)
    for k in range(n - 1, -1, -1):
        out[k] = 2 ** cand[k, j]
        j = back[k, j]
    return out


def _downbeats_from_features(beats: np.ndarray, y: np.ndarray, sr: int, stem_path: Path | None) -> np.ndarray:
    """Pick the bar positions (meter 3 or 4, phase may shift at odd bars) with a Viterbi pass over
    per-beat evidence: harmonic change in the mix, bass-note change and attacks, low-end hits."""
    import librosa

    hop = 512  # 23 ms is plenty for per-beat evidence, and 2x faster than the beat tracker's hop
    frames = librosa.time_to_frames(beats, sr=sr, hop_length=hop)
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop))
    harmonic, _ = librosa.decompose.hpss(S, kernel_size=17, margin=2.0)  # chords without the drums
    chroma = librosa.feature.chroma_stft(S=harmonic ** 2, sr=sr, hop_length=hop)
    low_on = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[:24]), sr=sr, hop_length=hop)

    def change(feat: np.ndarray) -> np.ndarray:
        sync = librosa.util.sync(feat, frames, aggregate=np.median)  # column k: beat k-1 .. k
        sync = sync / (np.linalg.norm(sync, axis=0, keepdims=True) + 1e-9)
        out = np.zeros(len(beats))
        for k in range(len(beats)):
            a, b = sync[:, k], sync[:, min(k + 1, sync.shape[1] - 1)]
            out[k] = 1 - float(a @ b)  # change from the beat before to the beat starting here
        return out

    def at_beats(curve: np.ndarray) -> np.ndarray:
        return np.array([curve[max(0, f - 2):f + 3].max() if f < len(curve) else 0.0 for f in frames])

    feats = [change(chroma), at_beats(low_on)]
    if stem_path is not None:
        ys = audio_io.load(stem_path, sr=sr, mono=True)[0]
        C = np.abs(librosa.cqt(ys, sr=sr, hop_length=hop, fmin=librosa.midi_to_hz(24), n_bins=48))
        feats.append(change(C))
        feats.append(at_beats(librosa.onset.onset_strength(y=ys, sr=sr, hop_length=hop)))
    z = sum((f - f.mean()) / (f.std() + 1e-9) for f in feats) / len(feats)

    best = None
    for meter in (4, 3):
        score, path = _bar_viterbi(z, meter)
        # Normalise: average evidence per downbeat (more downbeats in 3/4 must not win by count)
        per_bar = score / max(1, sum(1 for s_ in path if s_ == 0))
        if meter == 3:
            per_bar *= 0.85  # 4/4 is far more common; 3/4 must be clearly better
        if best is None or per_bar > best[0]:
            best = (per_bar, meter, path)
    _, meter, path = best
    if meter == 3:
        info("Meter guess: 3/4")
    return beats[np.array(path) == 0]


def _bar_viterbi(z: np.ndarray, meter: int, short_bar_penalty: float = 2.5) -> tuple[float, list[int]]:
    """States = position of each beat in its bar (0 = downbeat). A bar may end early (odd bar,
    pickup) at a penalty; it never runs longer than the meter."""
    n = len(z)
    NEG = -1e18
    score = np.full((n, meter), NEG)
    back = np.zeros((n, meter), int)
    score[0] = [z[0] if p == 0 else 0.0 for p in range(meter)]
    for k in range(1, n):
        for p in range(meter):
            if p == 0:
                cands = [(score[k - 1, meter - 1], meter - 1)] + \
                        [(score[k - 1, q] - short_bar_penalty, q) for q in range(meter - 1)]
                v, q = max(cands)
                score[k, 0] = v + z[k]
                back[k, 0] = q
            else:
                score[k, p] = score[k - 1, p - 1]
                back[k, p] = p - 1
    p = int(np.argmax(score[-1]))
    total = float(score[-1, p])
    path = [p]
    for k in range(n - 1, 0, -1):
        p = int(back[k, p])
        path.append(p)
    return total, path[::-1]


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
    db = _regularize_bars(db)
    # Beats before the first downbeat belong to a pickup measure; make sure beat 0 starts one.
    db[0] = True
    return beats, db


def _regularize_bars(db: np.ndarray) -> np.ndarray:
    """Fill in downbeats the tracker missed: a bar exactly 2x/3x the usual length is split."""
    idx = np.flatnonzero(db)
    if len(idx) < 3:
        return db
    lengths = np.diff(idx)
    vals, counts = np.unique(lengths, return_counts=True)
    usual = int(vals[np.argmax(counts)])
    if usual < 2:
        return db
    out = db.copy()
    for start, length in zip(idx[:-1], lengths):
        if length > usual and length % usual == 0:
            out[start + usual:start + length:usual] = True
    return out


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
           song_length: float | None = None, checkpoint: str = "final0", stem_path: Path | None = None) -> BeatMap:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = audio_io.file_hash(mix_path, f"beats:{tracker}:{checkpoint}:v5")
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
            if "Could not load the checkpoint" in str(e):
                reason = ("its model isn't downloaded yet and the download server (cloud.cp.jku.at) "
                          "couldn't be reached - run setup.bat again later")
            else:
                reason = f"{e.__class__.__name__}: {e}"
            warn(f"Beat This! not used ({reason}). Using the simpler librosa tracker instead; "
                 "check the clicks in the check mix.")
            beats, downbeats = _track_librosa(mix_path, stem_path)
            source = "librosa"
    else:
        beats, downbeats = _track_librosa(mix_path, stem_path)

    beats, db = _clean(beats, downbeats)
    beats = _refine(beats, mix_path)
    keep = beats > 0.0  # the latency correction can push a very early beat below zero
    beats, db = beats[keep], db[keep]
    db[0] = True
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
