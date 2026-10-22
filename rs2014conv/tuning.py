"""Tunings: names, Rocksmith offsets, and guessing a tuning from detected notes."""

from __future__ import annotations

import re
from collections import Counter

# Standard 4-string bass E1 A1 D2 G2 and 5-string B0 E1 A1 D2 G2 as MIDI numbers.
E_STANDARD_4 = [28, 33, 38, 43]
B_STANDARD_5 = [23, 28, 33, 38, 43]

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_TO_SHARP = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}

# name -> semitone offsets for each string of a 4-string bass relative to E standard
NAMED_4 = {
    "e standard": [0, 0, 0, 0],
    "drop d": [-2, 0, 0, 0],
    "eb standard": [-1, -1, -1, -1],
    "d# standard": [-1, -1, -1, -1],
    "drop c#": [-3, -1, -1, -1],
    "d standard": [-2, -2, -2, -2],
    "drop c": [-4, -2, -2, -2],
    "c# standard": [-3, -3, -3, -3],
    "db standard": [-3, -3, -3, -3],
    "drop b": [-5, -3, -3, -3],
    "c standard": [-4, -4, -4, -4],
    "b standard": [-5, -5, -5, -5],
    "f standard": [1, 1, 1, 1],
}


def note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def parse_note(text: str) -> int:
    """'E1' -> 28, 'Bb0' -> 22."""
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", text.strip())
    if not m:
        raise ValueError(f"Not a note name: {text!r}")
    name = (m.group(1) + m.group(2)).upper()
    name = _FLAT_TO_SHARP.get(name, name)
    return NOTE_NAMES.index(name) + 12 * (int(m.group(3)) + 1)


def parse_tuning(text: str, strings: int = 4) -> tuple[list[int], str]:
    """Accepts a name ("Drop D", "D Standard") or notes low->high ("B E A D G" / "D1 A1 D2 G2").

    Returns (open string MIDI pitches low->high, display name).
    """
    key = " ".join(text.lower().replace("std", "standard").split())
    if key in NAMED_4:
        offsets = NAMED_4[key]
        base = [b + o for b, o in zip(E_STANDARD_4, offsets)]
        if strings == 5:
            base = [base[0] - 5] + base
        return base, text.strip()
    parts = text.replace(",", " ").split()
    if len(parts) in (4, 5, 6):
        if all(re.search(r"\d", p) for p in parts):
            pitches = [parse_note(p) for p in parts]
        else:
            # Letters only: put them in the usual bass register, ascending.
            ref = B_STANDARD_5 if len(parts) == 5 else E_STANDARD_4
            pitches = []
            for p, r in zip(parts, ref):
                pc = NOTE_NAMES.index(_FLAT_TO_SHARP.get(p.upper(), p.upper()))
                cand = min((pc + 12 * o for o in range(0, 5)), key=lambda x: abs(x - r) + (0.1 if x > r else 0))
                pitches.append(cand)
        return pitches, " ".join(note_name(p) for p in pitches)
    raise ValueError(f"Unknown tuning {text!r}. Use e.g. 'E Standard', 'Drop D' or 'D A D G'.")


def describe(tuning: list[int]) -> str:
    if len(tuning) == 4:
        offsets = [t - b for t, b in zip(tuning, E_STANDARD_4)]
        for name, off in NAMED_4.items():
            if off == offsets and name not in ("d# standard", "db standard"):
                return name.title()
    return " ".join(note_name(p) for p in tuning)


def rocksmith_offsets(tuning: list[int]) -> tuple[list[int], list[str]]:
    """Rocksmith wants a 4-string bass: per-string semitone offsets relative to E A D G.

    A 5-string tuning is charted on a 4-string layout tuned down (e.g. B E A D G -> B E A D,
    shown in game as B standard). Returns (6 offsets, notes about what was done).
    """
    notes: list[str] = []
    strings = list(tuning)
    if len(strings) == 5:
        strings = strings[:4]
        notes.append(
            "5-string tuning: charted on the lowest four strings "
            f"({' '.join(note_name(p) for p in strings)}); notes on the top string were moved "
            "to the next string down. Rocksmith shows this as a down-tuned 4-string bass."
        )
    elif len(strings) == 6:
        strings = strings[:4]
        notes.append("6-string tuning: only the lowest four strings are used.")
    offsets = [s - b for s, b in zip(strings, E_STANDARD_4)]
    return offsets + [0] * (6 - len(offsets)), notes


def guess_tuning(pitches: list[int], strings: int = 4) -> tuple[list[int], str, str]:
    """Guess a tuning from detected note pitches. Returns (tuning, name, explanation)."""
    if not pitches:
        base, name = parse_tuning("E Standard", strings)
        return base, name, "no notes detected; assuming E Standard"
    ordered = sorted(pitches)
    # The lowest note that is really played: a few stray low notes (slides, kick-drum bleed,
    # octave errors) must not change the tuning, so it has to make up 2% of the notes.
    counts = Counter(ordered)
    need = max(2, int(0.02 * len(ordered))) if len(ordered) >= 30 else 1
    candidates = [p for p in sorted(counts) if counts[p] >= need]
    lowest = candidates[0] if candidates else ordered[len(ordered) // 2]

    if strings == 5 or lowest <= 23:
        if lowest >= 23:
            return B_STANDARD_5, "B Standard (5-string)", f"lowest note {note_name(lowest)}"
        t = [p - (23 - lowest) for p in B_STANDARD_5]
        return t, describe(t) + " (5-string)", f"lowest note {note_name(lowest)}"

    if lowest >= 28:
        name = "E Standard"
    elif lowest == 27:
        name = "Eb Standard"
    elif lowest == 26:
        # Drop D vs D standard: in D standard the top string is C2 (36); in Drop D you
        # still have the A string (33). Look at what else is played around that note.
        eb_band = sum(counts[p] for p in (26, 27))
        name = "Drop D" if counts[27] < max(2, eb_band // 4) else "D Standard"
    elif lowest == 25:
        name = "C# Standard"
    else:
        name = "Drop C" if counts[25] == 0 else "C Standard"
    base, disp = parse_tuning(name, 4)
    return base, disp, f"lowest note {note_name(lowest)}"


def estimate_reference_cents(stem_path) -> float:
    """How far the recording's tuning is from A=440 Hz, in cents (e.g. -32 for A=432 Hz).

    Measured on the bass stem with librosa's pitch histogram, so tape-speed drift on old records
    or a band tuning to something other than 440 is picked up.
    """
    import librosa

    from . import audio_io

    y = audio_io.load(stem_path, sr=22050, mono=True)[0]
    if float(abs(y).max()) < 1e-4:
        return 0.0
    frac = librosa.estimate_tuning(y=y, sr=22050, n_fft=8192, resolution=0.01, bins_per_octave=12,
                                   fmin=38.0, fmax=800.0)
    return float(frac) * 100.0


def cents_to_pitch(cents: float) -> float:
    return 440.0 * 2 ** (cents / 1200.0)
