"""Settings: config.yaml (global defaults) <- song.yaml (per song) <- command line."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict = {
    "charter_name": "make_cdlc",
    "output_dir": "",
    "cache_dir": str(ROOT / "cache"),
    "device": "auto",
    "strings": 4,
    "bass_picked": False,
    "lead_in_seconds": 2.0,
    "tuning_pitch": "auto",
    "sync_to_bass": True,
    "separation": {"model": "htdemucs_ft", "shifts": 1},
    "beats": {"tracker": "beat_this", "checkpoint": "final0"},
    "transcription": {
        "pitch_method": "crepe",
        "grid": "1/16",
        "min_note": 0.06,
        "min_confidence": 0.45,
        "voicing_threshold": 0.35,
        "onset_sensitivity": 0.5,
        "min_sustain_beats": 0.75,
    },
    "tab": {"min_sustain_beats": 1.0, "detect_tuning_shift": True, "snap_to_onsets": False,
            "snap_tolerance": 0.04},
    "fingering": {"max_fret": 24, "shift_cost": 1.0, "height_cost": 0.01, "string_jump_cost": 0.3,
                  "open_string_bonus": 0.15},
    "sections": {"auto_detect": True, "min_bars": 8, "phrase_bars": 8},
    "check_mix": {"clicks": True, "synth_gain_db": 0.0, "split_file": True, "format": "wav"},
    "packaging": {
        "enabled": True,
        "packager_path": "",
        "wwise_console": "",
        "auto_volume": True,
        "mac": False,
        "copy_to_folder": "",
    },
}

SONG_KEYS = {"audio", "title", "artist", "album", "year", "tab", "track", "stem", "tuning", "strings",
             "cover", "grid", "bass_picked", "version", "output_dir"}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(config_path: Path | None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    path = config_path or (ROOT / "config.yaml")
    if path.exists():
        cfg = _merge(cfg, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    return cfg


def load_song_yaml(folder: Path) -> dict:
    for name in ("song.yaml", "song.yml"):
        p = folder / name
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            unknown = set(data) - SONG_KEYS - set(DEFAULTS)
            if unknown:
                raise ValueError(f"Unknown keys in {p.name}: {', '.join(sorted(unknown))}")
            return data
    return {}
