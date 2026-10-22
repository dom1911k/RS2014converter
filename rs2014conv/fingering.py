"""String/fret assignment by dynamic programming, plus Rocksmith hand anchors.

State = (string, fret, hand position). The hand position is the fret under the index finger;
it covers four frets (one finger per fret). Open strings don't move the hand. Costs:
  * moving the hand (more expensive when notes are close together in time)
  * playing high up the neck (favours low positions)
  * string jumps (small)
  * open strings are free when the hand is low on the neck, slightly penalised higher up
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Note


@dataclass
class FingeringConfig:
    max_fret: int = 24
    span: int = 4
    shift_cost: float = 1.0
    shift_per_fret: float = 0.25
    height_cost: float = 0.06
    string_jump_cost: float = 0.12
    open_string_bonus: float = 0.15
    high_open_penalty: float = 0.4


def _candidates(pitch: int, tuning: list[int], max_fret: int) -> list[tuple[int, int]]:
    return [(s, pitch - open_p) for s, open_p in enumerate(tuning) if 0 <= pitch - open_p <= max_fret]


def assign(notes: list[Note], tuning: list[int], cfg: FingeringConfig | None = None,
           keep_given: bool = False) -> list[int]:
    """Assign string/fret to every note (in place). Returns the hand position per note.

    keep_given=True keeps the string/fret already on each note (from a tab) and only
    computes hand positions.
    """
    cfg = cfg or FingeringConfig()
    if not notes:
        return []
    INF = float("inf")
    # layers[i] = {state: (cost, prev_state)}; state = (string, fret, hand)
    layers: list[dict[tuple[int, int, int], tuple[float, tuple | None]]] = []
    prev_time = None
    for i, n in enumerate(notes):
        if keep_given and n.string >= 0 and n.fret >= 0:
            cands = [(n.string, n.fret)]
        else:
            cands = _candidates(n.pitch, tuning, cfg.max_fret)
            if not cands:
                # Out of range: move by octaves until playable.
                p = n.pitch
                while p < tuning[0]:
                    p += 12
                while p > tuning[-1] + cfg.max_fret:
                    p -= 12
                n.pitch = p
                cands = _candidates(p, tuning, cfg.max_fret)
        gap = 1.0 if prev_time is None else max(0.05, n.time - prev_time)
        urgency = 1.0 + 0.35 / gap
        prev_time = n.time

        layer: dict[tuple[int, int, int], tuple[float, tuple | None]] = {}
        prev_layer = layers[-1] if layers else None
        for s, f in cands:
            if f == 0:
                hands = sorted({st[2] for st in prev_layer}) if prev_layer else [1]
            else:
                hands = [h for h in range(f - cfg.span + 1, f + 1) if h >= 1]
            for h in hands:
                local = cfg.height_cost * (h - 1)
                if f == 0:
                    local += -cfg.open_string_bonus if h <= 5 else cfg.high_open_penalty
                # Prefer index on the lowest fret of a position (finger = f - h)
                if f > 0:
                    local += 0.02 * (f - h)
                best = (INF, None)
                if prev_layer is None:
                    best = (local, None)
                else:
                    for ps, (pc, _) in prev_layer.items():
                        ph = ps[2]
                        c = pc + local + cfg.string_jump_cost * abs(ps[0] - s)
                        if h != ph and f != 0:
                            c += urgency * (cfg.shift_cost + cfg.shift_per_fret * abs(h - ph))
                        if c < best[0]:
                            best = (c, ps)
                key = (s, f, h)
                if key not in layer or best[0] < layer[key][0]:
                    layer[key] = best
        layers.append(layer)

    # Backtrack
    state = min(layers[-1].items(), key=lambda kv: kv[1][0])[0]
    hands = [0] * len(notes)
    for i in range(len(notes) - 1, -1, -1):
        s, f, h = state
        notes[i].string, notes[i].fret = s, f
        hands[i] = h
        state = layers[i][state][1]
    # Slides: the destination fret also needs to be reachable; fine for anchors (approx).
    return hands


def anchors(notes: list[Note], hands: list[int], span: int = 4) -> list[tuple[float, int, int]]:
    """Rocksmith anchors (time, fret, width) from the hand positions."""
    out: list[tuple[float, int, int]] = []
    cur = None
    for n, h in zip(notes, hands):
        h = max(1, h)
        if cur != h:
            out.append((n.time, h, span))
            cur = h
    return out
