"""Bass stem separation with Demucs (htdemucs_ft bass model), cached on disk."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import audio_io
from .log import info


def separate_bass(mix_path: Path, cache_dir: Path, model_name: str = "htdemucs_ft",
                  device: str = "auto", shifts: int = 1) -> Path:
    """Returns the path of a cached 44.1 kHz stereo wav with the bass stem."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = audio_io.file_hash(mix_path, f"{model_name}:{shifts}")
    out = cache_dir / f"bass_{key}.wav"
    if out.exists():
        info(f"Bass stem: using cached separation ({out.name})")
        return out

    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    info(f"Separating bass with Demucs {model_name} on {device} (first time only, takes a while)...")
    bag = get_model(model_name)
    model = bag
    # htdemucs_ft is a bag of 4 specialised models; only run the bass specialist.
    if hasattr(bag, "models") and hasattr(bag, "weights") and "bass" in bag.sources:
        bi = bag.sources.index("bass")
        for sub, w in zip(bag.models, bag.weights):
            if len(w) == len(bag.sources) and w[bi] == max(w) and sum(w) == w[bi]:
                model = sub
                break
    sr = model.samplerate
    mix = audio_io.load(mix_path, sr=sr, mono=False)
    wav = torch.from_numpy(mix)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        sources = apply_model(model, ((wav - mean) / std)[None], device=device, shifts=shifts,
                              split=True, overlap=0.25, progress=True)[0]
    sources = sources * std + mean
    bass = sources[model.sources.index("bass")].cpu().numpy()
    audio_io.save_wav(out, bass, sr, subtype="FLOAT")
    info(f"Bass stem saved to cache ({out.name})")
    return out
