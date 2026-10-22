"""Audio loading/saving. Uses ffmpeg (bundled via imageio-ffmpeg if not on PATH)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("ffmpeg not found. Run setup again or install ffmpeg.") from e


def load(path: str | Path, sr: int = 44100, mono: bool = False) -> np.ndarray:
    """Decode any audio file to float32, shape (channels, samples)."""
    channels = 1 if mono else 2
    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", str(channels), "-ar", str(sr), "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    data = np.frombuffer(raw, dtype=np.float32).reshape(-1, channels).T
    return np.ascontiguousarray(data)


def save_wav(path: str | Path, audio: np.ndarray, sr: int = 44100, subtype: str = "PCM_16") -> None:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio[None]
    sf.write(str(path), np.clip(audio.T, -1.0, 1.0), sr, subtype=subtype)


def file_hash(path: str | Path, extra: str = "") -> str:
    h = hashlib.sha1(extra.encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def duration(path: str | Path) -> float:
    return load(path, sr=8000, mono=True).shape[1] / 8000.0
