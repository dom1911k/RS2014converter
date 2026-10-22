"""Bass transcription (no-tab fallback): pitch track + onsets -> notes -> cleaned, quantized notes.

Bass is treated as monophonic. Pitch comes from CREPE (torchcrepe, GPU if available) with
pYIN as a fallback; note starts come from onset detection on the stem plus stable pitch
changes (for legato notes without a new attack).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import audio_io
from .beats import BeatMap
from .log import info, warn
from .model import Note

SR = 16000
HOP = 80  # 5 ms


@dataclass
class TranscribeConfig:
    pitch_method: str = "crepe"  # crepe | pyin
    device: str = "auto"
    min_note: float = 0.06  # seconds; shorter notes are dropped
    min_confidence: float = 0.45
    voicing_threshold: float = 0.35
    onset_sensitivity: float = 0.5  # 0..1, higher = more onsets
    merge_gap: float = 0.035
    grid: str = "1/16"  # 1/8, 1/16, 1/8T, 1/16T, 1/16+T (mixed straight/triplet)
    min_sustain_beats: float = 0.75
    lowest_pitch: int = 23  # B0
    highest_pitch: int = 67  # G4 (24th fret G string)


# --------------------------------------------------------------------------- pitch

def _pitch_crepe(y: np.ndarray, device: str) -> tuple[np.ndarray, np.ndarray]:
    import torch
    import torchcrepe

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    audio = torch.from_numpy(y.astype(np.float32))[None]
    # fmin must not be below CREPE's lowest bin (31.7 Hz) or torchcrepe masks every bin.
    # Low B (30.9 Hz) is then found an octave up and corrected by fix_octaves().
    f0, per = torchcrepe.predict(audio, SR, HOP, 32.0, 420.0, "full", batch_size=1024,
                                 device=device, return_periodicity=True,
                                 decoder=torchcrepe.decode.weighted_argmax)
    # Not the Viterbi decoder: it only allows ~2.4 semitones of movement per frame, which loses
    # octave jumps (very common in bass lines). A short median filter removes the jitter instead.
    per = torchcrepe.filter.median(per, 3)
    f0 = torchcrepe.filter.median(torch.nan_to_num(f0, nan=0.0), 5)
    return f0[0].cpu().numpy(), per[0].cpu().numpy()


def _pitch_pyin(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    import librosa

    f0, _, prob = librosa.pyin(y, fmin=30.0, fmax=420.0, sr=SR, frame_length=2048, hop_length=HOP,
                               fill_na=0.0)
    return np.nan_to_num(f0), prob


def pitch_track(y: np.ndarray, method: str, device: str, cache_file=None) -> tuple[np.ndarray, np.ndarray, str]:
    if cache_file is not None and cache_file.exists():
        d = np.load(cache_file)
        info(f"Pitch track: using cached result ({cache_file.name})")
        return d["f0"], d["conf"], str(d["method"])
    f0, conf, used = _pitch_track(y, method, device)
    if cache_file is not None and used == method:
        np.savez(cache_file, f0=f0, conf=conf, method=used)
    return f0, conf, used


def _pitch_track(y: np.ndarray, method: str, device: str) -> tuple[np.ndarray, np.ndarray, str]:
    if method == "crepe":
        try:
            info("Pitch tracking with CREPE...")
            f0, conf = _pitch_crepe(y, device)
            return f0, conf, "crepe"
        except Exception as e:  # noqa: BLE001
            warn(f"CREPE pitch tracking failed ({e}); falling back to pYIN (slower).")
    info("Pitch tracking with pYIN...")
    f0, conf = _pitch_pyin(y)
    return f0, conf, "pyin"


def hz_to_midi(f: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(f > 0, 69 + 12 * np.log2(f / 440.0), np.nan)


# --------------------------------------------------------------------------- spectrum helper

class BassSpectrum:
    """Magnitude CQT of the stem in the bass register, for octave checks."""

    def __init__(self, y: np.ndarray):
        import librosa

        self.fmin_midi = 21  # A0
        self.bins_per_semitone = 3
        self.hop = 256
        C = librosa.cqt(y, sr=SR, hop_length=self.hop, fmin=librosa.midi_to_hz(self.fmin_midi),
                        n_bins=self.bins_per_semitone * 60, bins_per_octave=12 * self.bins_per_semitone)
        self.mag = np.abs(C)

    def energy(self, midi: int, t0: float, t1: float) -> float:
        b = (midi - self.fmin_midi) * self.bins_per_semitone
        if b < 0 or b >= self.mag.shape[0]:
            return 0.0
        f0 = int(t0 * SR / self.hop)
        f1 = max(f0 + 1, int(t1 * SR / self.hop))
        lo, hi = max(0, b - 1), min(self.mag.shape[0], b + 2)
        return float(self.mag[lo:hi, f0:f1].max(0).mean()) if f1 <= self.mag.shape[1] else 0.0


# --------------------------------------------------------------------------- segmentation

def _onsets(y: np.ndarray, sensitivity: float) -> np.ndarray:
    """Note attacks: spectral-flux onsets plus energy re-attacks (repeated notes of the same pitch)."""
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, n_mels=64, fmax=4000, lag=2, max_size=3)
    delta = 0.03 + 0.25 * (1 - sensitivity)
    wait = int(0.05 * SR / HOP)
    spectral = librosa.onset.onset_detect(onset_envelope=env, sr=SR, hop_length=HOP, backtrack=True,
                                          delta=delta, wait=wait, units="frames")
    # Energy envelope (dB) with ~10 ms resolution; a rise of a few dB within 20 ms is an attack.
    frame = 400
    rms = librosa.feature.rms(y=y, frame_length=frame, hop_length=HOP, center=True)[0]
    db = 20 * np.log10(rms / (rms.max() + 1e-12) + 1e-9)
    lag = 4
    rise = np.zeros_like(db)
    rise[lag:] = db[lag:] - np.minimum.accumulate(
        np.lib.stride_tricks.sliding_window_view(np.pad(db, (lag, 0), mode="edge"), lag + 1)[:-lag].T)[-1]
    thr = 3.0 + 4.0 * (1 - sensitivity)
    energy = []
    i = 0
    while i < len(rise):
        if rise[i] > thr and db[i] > -40:
            j = i
            while j + 1 < len(rise) and rise[j + 1] >= rise[j]:
                j += 1
            # Backtrack to the start of the rise
            k = j
            while k > 0 and db[k - 1] < db[k] - 0.3:
                k -= 1
            energy.append(k)
            i = j + wait
        else:
            i += 1
    allf = np.sort(np.concatenate([spectral, np.array(energy, dtype=int)]))
    out: list[int] = []
    for f in allf:
        if not out or f - out[-1] > int(0.04 * SR / HOP):
            out.append(int(f))
    return np.array(out, dtype=int)


def _segment(midi: np.ndarray, voiced: np.ndarray, conf: np.ndarray, rms: np.ndarray,
             onset_frames: np.ndarray, cfg: TranscribeConfig) -> list[Note]:
    n = len(midi)
    boundaries = np.zeros(n + 1, bool)
    boundaries[np.clip(onset_frames, 0, n)] = True
    # Stable pitch changes inside voiced regions (legato, slides) also start new notes.
    sm = midi.copy()
    sm[~voiced] = np.nan
    win = 8  # 40 ms
    for i in range(win, n - win):
        if not (voiced[i - win:i].all() and voiced[i:i + win].all()):
            continue
        a = np.nanmedian(sm[i - win:i])
        b = np.nanmedian(sm[i:i + win])
        if abs(round(a) - round(b)) >= 1 and abs(a - b) > 0.7:
            if abs(np.nanmedian(sm[i - 1:i + 1]) - b) <= abs(np.nanmedian(sm[i - 1:i + 1]) - a):
                boundaries[i] = True
    # Voicing starts are boundaries as well.
    starts = np.flatnonzero(voiced[1:] & ~voiced[:-1]) + 1
    boundaries[starts] = True
    if voiced[0]:
        boundaries[0] = True

    notes: list[Note] = []
    idx = np.flatnonzero(boundaries)
    idx = idx[idx < n]
    for k, s in enumerate(idx):
        e = idx[k + 1] if k + 1 < len(idx) else n
        seg = slice(s, e)
        v = voiced[seg]
        if v.sum() < 3:
            continue
        # Note ends where voicing stops for good (allow tiny gaps).
        vi = np.flatnonzero(v)
        end = s + vi[-1] + 1
        start = s + vi[0] if vi[0] < 6 else s
        pitches = midi[seg][v]
        # Use the stable middle part of the note for its pitch.
        core = pitches[len(pitches) // 5: max(len(pitches) // 5 + 1, 4 * len(pitches) // 5)]
        p = int(round(float(np.median(core))))
        c = float(np.mean(conf[seg][v]))
        notes.append(Note(time=start * HOP / SR, sustain=(end - start) * HOP / SR, pitch=p, confidence=c))
    return notes


# --------------------------------------------------------------------------- post-processing

def fix_octaves(notes: list[Note], spec: BassSpectrum, lowest: int, highest: int) -> int:
    """Correct octave errors. Returns the number of changed notes."""
    changed = 0
    for i, n in enumerate(notes):
        orig = n.pitch
        while n.pitch < lowest:
            n.pitch += 12
        while n.pitch > highest:
            n.pitch -= 12
        # Pitch trackers often lock onto the 2nd harmonic of low notes. If there is clear
        # energy one octave down (and that's playable), the real note is probably lower.
        low = n.pitch - 12
        if low >= lowest:
            # Look at the body of the note only: the previous note may still ring at its start.
            a, b = n.time + 0.3 * n.sustain, n.time + 0.9 * n.sustain
            e_hi = spec.energy(n.pitch, a, b)
            e_lo = spec.energy(low, a, b)
            # The lower note's 3rd harmonic (fifth above n.pitch) confirms it.
            e_fifth = spec.energy(n.pitch + 7, a, b)
            if e_hi > 0 and e_lo > 0.45 * e_hi and e_fifth > 0.15 * e_hi:
                n.pitch = low
        # Isolated octave jumps between two close neighbours are errors only when the pitch tracker
        # was unsure (real octave pops are common in bass lines).
        if 0 < i < len(notes) - 1 and n.sustain < 0.2 and n.confidence < 0.6:
            a, b = notes[i - 1].pitch, notes[i + 1].pitch
            if abs(a - b) <= 2:
                for cand in (n.pitch - 12, n.pitch + 12):
                    if abs(cand - a) <= 3 and lowest <= cand <= highest and abs(n.pitch - a) >= 10:
                        n.pitch = cand
                        break
        if n.pitch != orig:
            changed += 1
    return changed


def merge_fragments(notes: list[Note], gap: float, onset_times: np.ndarray) -> list[Note]:
    out: list[Note] = []
    for n in notes:
        if out:
            p = out[-1]
            g = n.time - p.end
            has_attack = np.any(np.abs(onset_times - n.time) < 0.02)
            if p.pitch == n.pitch and g < gap and not has_attack:
                p.sustain = n.end - p.time
                p.confidence = max(p.confidence, n.confidence)
                continue
        out.append(n)
    return out


def drop_weak(notes: list[Note], min_len: float, min_conf: float) -> list[Note]:
    return [n for n in notes if n.sustain >= min_len and n.confidence >= min_conf]


def _grid_options(grid: str) -> list[list[float]]:
    """Possible subdivisions of one beat (as fractions) for the grid setting."""
    g = grid.replace(" ", "").lower()
    straight = {"1/4": 1, "1/8": 2, "1/16": 4, "1/32": 8}
    trip = {"1/8t": 3, "1/12": 3, "1/16t": 6, "1/24": 6}
    if "+" in g:
        a, _ = g.split("+", 1)
        s = straight.get(a, 4)
        return [[k / s for k in range(s)], [k / 3 for k in range(3)]]
    if g in straight:
        s = straight[g]
        return [[k / s for k in range(s)]]
    if g in trip:
        s = trip[g]
        return [[k / s for k in range(s)]]
    raise ValueError(f"Unknown grid {grid!r} (use 1/8, 1/16, 1/8T, 1/16T or 1/16+T)")


def quantize(notes: list[Note], bm: BeatMap, grid: str, min_sustain_beats: float) -> list[Note]:
    """Snap note starts/ends to the beat map grid. Mixed grids choose per beat."""
    options = _grid_options(grid)
    if not notes:
        return notes
    starts = bm.beat_at(np.array([n.time for n in notes]))
    ends = bm.beat_at(np.array([n.end for n in notes]))

    # Per beat, choose the subdivision family that fits the onsets inside it best
    # (with a small bias towards the first/straight option).
    beat_choice: dict[int, list[float]] = {}
    beat_ids = np.floor(starts).astype(int)
    for b in np.unique(beat_ids):
        frac = starts[beat_ids == b] - b
        best, best_cost = options[0], None
        for k, opt in enumerate(options):
            grid_pts = np.array(opt + [1.0])
            cost = float(np.sum(np.min(np.abs(frac[:, None] - grid_pts[None]), axis=1) ** 2)) + 0.002 * k
            if best_cost is None or cost < best_cost:
                best, best_cost = opt, cost
        beat_choice[int(b)] = best

    def snap(x: float) -> float:
        b = int(np.floor(x))
        opt = beat_choice.get(b, options[0])
        pts = np.array(opt + [1.0])
        return b + float(pts[np.argmin(np.abs((x - b) - pts))])

    out: list[Note] = []
    finest = min(min(np.diff(o + [1.0])) for o in options)
    for n, s, e in zip(notes, starts, ends):
        qs = snap(s)
        qe = snap(e)
        if qe <= qs:
            qe = qs + finest
        n.time = float(bm.time_at(qs))
        length_beats = qe - qs
        n.sustain = float(bm.time_at(qe) - n.time) if length_beats >= min_sustain_beats else 0.0
        n._qbeat = qs  # type: ignore[attr-defined]
        n._qend = qe  # type: ignore[attr-defined]
        out.append(n)

    # Collisions on the same grid slot: keep the most confident note.
    dedup: dict[float, Note] = {}
    for n in out:
        k = round(n._qbeat, 4)  # type: ignore[attr-defined]
        if k not in dedup or n.confidence > dedup[k].confidence:
            dedup[k] = n
    res = sorted(dedup.values(), key=lambda n: n.time)
    # A sustain must not run into the next note.
    for a, b in zip(res, res[1:]):
        if a.sustain and a.end > b.time - 0.01:
            new = b.time - a.time - 0.05
            a.sustain = new if new >= 0.25 else 0.0
    return res


# --------------------------------------------------------------------------- main entry

def transcribe(stem_path, bm: BeatMap, cfg: TranscribeConfig, cache_dir=None) -> tuple[list[Note], dict]:
    y = audio_io.load(stem_path, sr=SR, mono=True)[0]
    peak = np.abs(y).max()
    if peak < 1e-4:
        warn("The bass stem is (almost) silent - no notes can be transcribed.")
        return [], {}
    y = y / peak * 0.9

    cache_file = None
    if cache_dir is not None:
        cache_file = cache_dir / f"pitch_{audio_io.file_hash(stem_path, cfg.pitch_method + ':v2')}.npz"
    f0, conf, method = pitch_track(y, cfg.pitch_method, cfg.device, cache_file)
    n = len(f0)
    midi = hz_to_midi(f0)
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(y, (HOP, HOP * 2)), HOP * 2)[::HOP][:n]
    rms = np.sqrt((frames ** 2).mean(1) + 1e-12)
    rms_db = 20 * np.log10(rms / rms.max())
    voiced = (conf >= cfg.voicing_threshold) & (rms_db > -42) & np.isfinite(midi)
    # Close 1-2 frame holes in voicing.
    for i in range(1, n - 2):
        if not voiced[i] and voiced[i - 1] and (voiced[i + 1] or voiced[i + 2]):
            if np.isfinite(midi[i]):
                voiced[i] = True

    onset_frames = _onsets(y, cfg.onset_sensitivity)
    onset_times = onset_frames * HOP / SR
    notes = _segment(midi, voiced, conf, rms, onset_frames, cfg)
    raw_count = len(notes)

    spec = BassSpectrum(y)
    octave_fixes = fix_octaves(notes, spec, cfg.lowest_pitch, cfg.highest_pitch)
    notes = merge_fragments(notes, cfg.merge_gap, onset_times)
    notes = drop_weak(notes, cfg.min_note, cfg.min_confidence)
    notes = merge_fragments(notes, cfg.merge_gap, onset_times)
    pre_q = len(notes)
    notes = quantize(notes, bm, cfg.grid, cfg.min_sustain_beats)
    stats = {"method": method, "raw_segments": raw_count, "octave_fixes": octave_fixes,
             "after_cleanup": pre_q, "final": len(notes)}
    info(f"Transcription ({method}): {raw_count} raw segments, {octave_fixes} octave fixes, "
         f"{len(notes)} notes after cleanup and quantization")
    return notes, stats
