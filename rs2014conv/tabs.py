"""Bass tab reading: Guitar Pro 3/4/5 (.gp3/.gp4/.gp5), Guitar Pro 6 (.gpx), Guitar Pro 7/8 (.gp)
and best-effort plain-text (ASCII) tab.

Everything is converted to a simple TabSong: measures with time signatures / repeats / markers,
and one track's notes with positions in quarter notes (q). Repeats are expanded by
`TabSong.playback()`.
"""

from __future__ import annotations

import io
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .log import info, warn
from .tuning import E_STANDARD_4

Q = 960  # ticks per quarter note (Guitar Pro convention)


@dataclass
class TabNote:
    start: float  # quarter notes from the start of its measure's first occurrence (score time)
    duration: float  # quarter notes
    string: int  # 0 = lowest string
    fret: int
    tie: bool = False
    dead: bool = False
    hammer_to_next: bool = False  # legato to the next note on the same string
    slide: str = ""  # "", "shift", "legato", "out_down", "out_up"
    palm_mute: bool = False
    harmonic: bool = False
    vibrato: bool = False
    accent: bool = False
    slap: bool = False
    pop: bool = False
    tap: bool = False
    tremolo: bool = False
    bend: float = 0.0
    ghost: bool = False


@dataclass
class TabMeasure:
    start: float  # quarter notes (score time)
    numerator: int
    denominator: int
    marker: str = ""
    repeat_open: bool = False
    repeat_close: int = 0  # number of repeats (play count - 1); 0 = no repeat sign
    alternatives: int = 0  # bitmask of alternate endings this measure belongs to
    direction: str = ""  # e.g. "Coda", "Segno", "Fine"
    jump: str = ""  # e.g. "DaCapo", "DaSegnoAlCoda"
    notes: list[TabNote] = field(default_factory=list)
    tempo: float | None = None  # tempo change at the start of this measure (quarter BPM)

    @property
    def length(self) -> float:
        return self.numerator * 4.0 / self.denominator

    @property
    def beat_length(self) -> float:
        """Length of one counted beat in quarter notes (dotted quarter for 6/8, 9/8, 12/8)."""
        if self.denominator >= 8 and self.numerator % 3 == 0 and self.numerator > 3:
            return 3 * 4.0 / self.denominator
        return 4.0 / self.denominator

    @property
    def beats(self) -> int:
        return max(1, int(round(self.length / self.beat_length)))


@dataclass
class TabSong:
    title: str
    artist: str
    tempo: float
    track_name: str
    tuning: list[int]  # MIDI pitches of open strings, low -> high
    measures: list[TabMeasure]
    source: str = ""

    def playback(self) -> list[int]:
        """Measure indices in playing order: repeats, alternate endings, D.C./D.S./Coda/Fine.

        After a D.C./D.S. jump, repeats are not taken again (the usual convention).
        """
        ms = self.measures
        segno = next((k for k, m in enumerate(ms) if "segno" in m.direction.lower()), 0)
        coda = next((k for k, m in enumerate(ms) if m.direction.lower() in ("coda", "doublecoda")), None)
        order: list[int] = []
        i, repeat_start, current_pass = 0, 0, 1
        taken: dict[int, int] = {}  # repeat-close measure -> times jumped back
        used_jumps: set[int] = set()
        jumped, arrived_by_repeat, fell_through = False, False, False
        while i < len(ms) and len(order) < 20000:
            m = ms[i]
            if m.repeat_open and not arrived_by_repeat:
                repeat_start, current_pass = i, 1
                taken = {k: v for k, v in taken.items() if k < i}
            arrived_by_repeat = False
            if m.alternatives:
                if not (m.alternatives >> (current_pass - 1)) & 1:
                    i += 1
                    continue
            elif fell_through:
                current_pass, fell_through = 1, False
            order.append(i)
            if jumped and "fine" in m.direction.lower():
                break
            if m.repeat_close and not jumped:
                if taken.get(i, 0) < m.repeat_close:
                    taken[i] = taken.get(i, 0) + 1
                    current_pass += 1
                    i, arrived_by_repeat = repeat_start, True
                    continue
                fell_through = True
            j = m.jump.lower().replace(" ", "")
            if jumped and coda is not None and i not in used_jumps and ("tocoda" in j or "dacoda" in j):
                used_jumps.add(i)
                i = coda
                continue
            if i not in used_jumps and ("dacapo" in j or "dasegno" in j):
                used_jumps.add(i)
                jumped = True
                i = segno if "segno" in j else 0
                continue
            i += 1
        return order


# --------------------------------------------------------------------------- Guitar Pro 3-5

def _read_gp345(path: Path, track_index: int | None) -> TabSong:
    import guitarpro

    song = guitarpro.parse(str(path))
    track = _choose_track(
        [(t.name, [s.value for s in sorted(t.strings, key=lambda s: -s.number)], t.isPercussionTrack,
          getattr(t.channel, "instrument", -1)) for t in song.tracks], track_index)
    t = song.tracks[track]
    strings_low_to_high = [s.value for s in sorted(t.strings, key=lambda s: -s.number)]
    nstr = len(strings_low_to_high)

    measures: list[TabMeasure] = []
    first_start = song.measureHeaders[0].start if song.measureHeaders else Q
    for h, meas in zip(song.measureHeaders, t.measures):
        tm = TabMeasure(
            start=(h.start - first_start) / Q,
            numerator=h.timeSignature.numerator,
            denominator=h.timeSignature.denominator.value,
            marker=h.marker.title if h.marker else "",
            repeat_open=h.isRepeatOpen,
            repeat_close=max(0, h.repeatClose),
            alternatives=h.repeatAlternative or 0,
            direction=h.direction.name if h.direction else "",
            jump=h.fromDirection.name if h.fromDirection else "",
        )
        for voice in meas.voices[:1] if len(meas.voices) else []:
            for beat in voice.beats:
                if beat.start is None:
                    continue
                bstart = (beat.start - first_start) / Q
                dur = beat.duration.time / Q
                mix = beat.effect.mixTableChange
                if mix is not None and mix.tempo is not None and mix.tempo.value and mix.tempo.value > 0:
                    if abs(bstart - tm.start) < 1e-6:
                        tm.tempo = float(mix.tempo.value)
                slap = beat.effect.slapEffect
                slap_name = getattr(slap, "name", "")
                for n in beat.notes:
                    if n.type.name == "rest":
                        continue
                    e = n.effect
                    slide = ""
                    for st in e.slides:
                        slide = {"shiftSlideTo": "shift", "legatoSlideTo": "legato",
                                 "outDownwards": "out_down", "outUpwards": "out_up"}.get(st.name, slide)
                    bend = 0.0
                    if e.bend is not None and e.bend.points:
                        bend = max(p.value for p in e.bend.points) * 1.0
                    tm.notes.append(TabNote(
                        start=bstart, duration=dur, string=nstr - n.string, fret=n.value,
                        tie=n.type.name == "tie", dead=n.type.name == "dead",
                        hammer_to_next=e.hammer, slide=slide, palm_mute=e.palmMute,
                        harmonic=e.harmonic is not None and getattr(e.harmonic, "type", 0) == 1,
                        vibrato=e.vibrato or beat.effect.vibrato, accent=e.accentuatedNote or e.heavyAccentuatedNote,
                        slap=slap_name == "slapping", pop=slap_name == "popping", tap=slap_name == "tapping",
                        tremolo=e.tremoloPicking is not None, bend=bend, ghost=e.ghostNote))
        measures.append(tm)
    if measures and measures[0].tempo is None:
        measures[0].tempo = float(song.tempo)
    return TabSong(song.title, song.artist, float(song.tempo), t.name, strings_low_to_high, measures,
                   source=path.suffix.lower())


def _choose_track(tracks: list[tuple[str, list[int], bool, int]], track_index: int | None) -> int:
    """Pick the bass track: explicit index, else bass-like name/instrument/tuning."""
    if track_index is not None:
        if not 0 <= track_index < len(tracks):
            raise ValueError(f"Track {track_index + 1} does not exist (the file has {len(tracks)} tracks)")
        return track_index
    best, best_score = 0, -1e9
    for i, (name, strings, perc, instrument) in enumerate(tracks):
        score = 0.0
        if perc:
            score -= 100
        if re.search(r"bass|bajo|basse|bas\b|baixo", name or "", re.I):
            score += 10
        if 32 <= instrument <= 39:  # General MIDI bass instruments
            score += 6
        if strings and min(strings) < 40:
            score += 4
        if len(strings) in (4, 5):
            score += 2
        if score > best_score:
            best, best_score = i, score
    info("Tab tracks: " + ", ".join(f"{i + 1}={t[0]!r}" for i, t in enumerate(tracks)) +
         f"  -> using track {best + 1} ({tracks[best][0]!r}); use --track N to choose another")
    return best


# --------------------------------------------------------------------------- Guitar Pro 6 (.gpx)

class _BitReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0  # byte position
        self.bit = 8
        self.cur = 0

    def read_bit(self) -> int:
        if self.bit >= 8:
            if self.pos >= len(self.data):
                raise EOFError
            self.cur = self.data[self.pos]
            self.pos += 1
            self.bit = 0
        v = (self.cur >> (7 - self.bit)) & 1
        self.bit += 1
        return v

    def read_bits(self, n: int) -> int:
        v = 0
        for i in range(n - 1, -1, -1):
            v |= self.read_bit() << i
        return v

    def read_bits_reversed(self, n: int) -> int:
        v = 0
        for i in range(n):
            v |= self.read_bit() << i
        return v


def _bcfz_decompress(data: bytes) -> bytes:
    expected = struct.unpack_from("<i", data, 0)[0]
    src = _BitReader(data[4:])
    out = bytearray()
    try:
        while len(out) < expected:
            if src.read_bits(1):
                word = src.read_bits(4)
                offset = src.read_bits_reversed(word)
                size = src.read_bits_reversed(word)
                pos = len(out) - offset
                for k in range(min(offset, size)):
                    out.append(out[pos + k])
            else:
                size = src.read_bits_reversed(2)
                for _ in range(size):
                    out.append(src.read_bits(8))
    except EOFError:
        pass
    return bytes(out)


def _bcfs_files(block: bytes) -> dict[str, bytes]:
    sector = 0x1000
    files: dict[str, bytes] = {}
    offset = sector
    while offset + 3 < len(block):
        if struct.unpack_from("<i", block, offset)[0] == 2:
            raw_name = block[offset + 4: offset + 4 + 127]
            name = raw_name.split(b"\0", 1)[0].decode("latin-1")
            size = struct.unpack_from("<i", block, offset + 0x8C)[0]
            ptr = offset + 0x94
            chunks = bytearray()
            k = 0
            while True:
                sec = struct.unpack_from("<i", block, ptr + 4 * k)[0]
                k += 1
                if sec == 0:
                    break
                chunks += block[sec * sector:(sec + 1) * sector]
            files[name] = bytes(chunks[:size])
        offset += sector
    return files


def _gpif_from_file(path: Path) -> bytes:
    raw = path.read_bytes()
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for name in z.namelist():
                if name.endswith("score.gpif"):
                    return z.read(name)
        raise ValueError("No score.gpif inside the .gp file")
    head = raw[:4]
    if head == b"BCFZ":
        block = _bcfz_decompress(raw[4:])[4:]  # skip the inner "BCFS" header
    elif head == b"BCFS":
        block = raw[4:]
    else:
        raise ValueError("Unrecognised Guitar Pro 6 file")
    files = _bcfs_files(block)
    if "score.gpif" not in files:
        raise ValueError("No score.gpif inside the .gpx file")
    return files["score.gpif"]


_NOTE_VALUES = {"Whole": 4.0, "Half": 2.0, "Quarter": 1.0, "Eighth": 0.5, "16th": 0.25, "32nd": 0.125,
                "64th": 0.0625, "128th": 0.03125, "256th": 0.015625, "DoubleWhole": 8.0}


def _props(el: ET.Element | None) -> dict[str, ET.Element]:
    out: dict[str, ET.Element] = {}
    if el is None:
        return out
    props = el.find("Properties")
    if props is not None:
        for p in props.findall("Property"):
            out[p.get("name", "")] = p
    return out


def _read_gpif(path: Path, track_index: int | None) -> TabSong:
    root = ET.fromstring(_gpif_from_file(path))
    score = root.find("Score")
    title = (score.findtext("Title") or "").strip() if score is not None else ""
    artist = (score.findtext("Artist") or "").strip() if score is not None else ""

    tracks_el = root.findall("Tracks/Track")
    track_info = []
    for t in tracks_el:
        name = (t.findtext("Name") or "").strip()
        pitches = None
        for holder in [t] + t.findall("Staves/Staff"):
            tp = _props(holder).get("Tuning")
            if tp is not None and tp.findtext("Pitches"):
                pitches = [int(x) for x in tp.findtext("Pitches").split()]
                break
        program = -1
        prog_text = t.findtext("GeneralMidi/Program") or t.findtext("MidiConnection/Program")
        if prog_text is None:
            sounds = t.find("Sounds/Sound/MIDI/Program")
            prog_text = sounds.text if sounds is not None else None
        if prog_text and prog_text.strip().lstrip("-").isdigit():
            program = int(prog_text)
        is_drum = t.find("InstrumentSet/Type") is not None and "drum" in (t.findtext("InstrumentSet/Type") or "").lower()
        track_info.append((name, pitches or [], is_drum, program))
    ti = _choose_track(track_info, track_index)
    tuning = track_info[ti][1] or E_STANDARD_4

    bars = {b.get("id"): b for b in root.findall("Bars/Bar")}
    voices = {v.get("id"): v for v in root.findall("Voices/Voice")}
    beats = {b.get("id"): b for b in root.findall("Beats/Beat")}
    notes = {n.get("id"): n for n in root.findall("Notes/Note")}
    rhythms = {r.get("id"): r for r in root.findall("Rhythms/Rhythm")}

    def rhythm_len(rid: str) -> float:
        r = rhythms[rid]
        v = _NOTE_VALUES.get(r.findtext("NoteValue") or "Quarter", 1.0)
        dot = r.find("AugmentationDot")
        if dot is not None:
            c = int(dot.get("count", "1"))
            v *= {1: 1.5, 2: 1.75}.get(c, 1.0)
        tup = r.find("PrimaryTuplet")
        if tup is not None:
            v *= int(tup.get("den")) / int(tup.get("num"))
        return v

    # Tempo automations: bar -> bpm (quarter)
    tempos: dict[int, float] = {}
    for a in root.findall("MasterTrack/Automations/Automation"):
        if (a.findtext("Type") or "") == "Tempo":
            vals = (a.findtext("Value") or "120").split()
            bpm = float(vals[0])
            unit = int(vals[1]) if len(vals) > 1 else 2
            bpm *= {1: 2.0, 2: 1.0, 3: 0.5, 4: 1.5, 5: 0.75}.get(unit, 1.0)  # note value of the tempo unit
            bar = int(a.findtext("Bar") or 0)
            tempos.setdefault(bar, bpm)

    measures: list[TabMeasure] = []
    pos = 0.0
    for mi, mb in enumerate(root.findall("MasterBars/MasterBar")):
        num, den = (mb.findtext("Time") or "4/4").split("/")
        rep = mb.find("Repeat")
        sec = mb.find("Section")
        dirs = mb.find("Directions")
        alt = mb.findtext("AlternateEndings")
        tm = TabMeasure(
            start=pos, numerator=int(num), denominator=int(den),
            marker=((sec.findtext("Text") or sec.findtext("Letter") or "").strip() if sec is not None else ""),
            repeat_open=rep is not None and rep.get("start") == "true",
            repeat_close=(max(1, int(rep.get("count", "2")) - 1) if rep is not None and rep.get("end") == "true" else 0),
            alternatives=sum(1 << (int(x) - 1) for x in alt.split()) if alt else 0,
            direction=(dirs.findtext("Target") or "") if dirs is not None else "",
            jump=(dirs.findtext("Jump") or "") if dirs is not None else "",
            tempo=tempos.get(mi),
        )
        bar_ids = (mb.findtext("Bars") or "").split()
        if ti < len(bar_ids) and bar_ids[ti] in bars:
            voice_ids = (bars[bar_ids[ti]].findtext("Voices") or "").split()
            if voice_ids and voice_ids[0] != "-1" and voice_ids[0] in voices:
                t = pos
                for bid in (voices[voice_ids[0]].findtext("Beats") or "").split():
                    b = beats[bid]
                    if b.find("GraceNotes") is not None:
                        continue
                    dur = rhythm_len(b.find("Rhythm").get("ref"))
                    bprops = _props(b)
                    for nid in (b.findtext("Notes") or "").split():
                        n = notes[nid]
                        p = _props(n)
                        if "String" not in p or "Fret" not in p:
                            continue
                        slide_flags = int(p["Slide"].findtext("Flags") or 0) if "Slide" in p else 0
                        slide = ("shift" if slide_flags & 1 else "legato" if slide_flags & 2 else
                                 "out_down" if slide_flags & 4 else "out_up" if slide_flags & 8 else "")
                        tie = n.find("Tie")
                        bend = 0.0
                        if "BendDestinationValue" in p or "BendMiddleValue" in p:
                            vals = [float(p[k].findtext("Float") or 0) for k in ("BendMiddleValue", "BendDestinationValue") if k in p]
                            bend = max(vals) / 50.0  # 100 = one whole tone
                        tm.notes.append(TabNote(
                            start=t, duration=dur, string=int(p["String"].findtext("String")),
                            fret=int(p["Fret"].findtext("Fret")),
                            tie=tie is not None and tie.get("destination") == "true",
                            dead="Muted" in p, hammer_to_next="HopoOrigin" in p, slide=slide,
                            palm_mute="PalmMuted" in p,
                            harmonic="HarmonicType" in p and (p["HarmonicType"].findtext("HType") or "") == "Natural",
                            vibrato=n.find("Vibrato") is not None or b.find("Vibrato") is not None,
                            accent=n.find("Accent") is not None,
                            slap="Slapped" in bprops, pop="Popped" in bprops, tap="Tapped" in p,
                            tremolo=b.find("Tremolo") is not None, bend=bend,
                            ghost=n.find("AntiAccent") is not None))
                    t += dur
        measures.append(tm)
        pos += tm.length
    tempo0 = tempos.get(0, 120.0)
    if measures and measures[0].tempo is None:
        measures[0].tempo = tempo0
    return TabSong(title, artist, tempo0, track_info[ti][0], tuning, measures, source=path.suffix.lower())


# --------------------------------------------------------------------------- ASCII tab

_STRING_LINE = re.compile(r"^\s*([A-Ga-g][#b]?)\s*[|:\]]?\s*([-0-9|xXhpbr/\\~sSvt().*<> ]+)\s*$")


def _read_text(path: Path) -> TabSong:
    """Best-effort ASCII tab: rhythm is estimated from character spacing within each bar."""
    lines = path.read_text(errors="replace").splitlines()
    systems: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    for line in lines + [""]:
        m = _STRING_LINE.match(line)
        if m and "-" in m.group(2):
            cur.append((m.group(1), m.group(2)))
        else:
            if 4 <= len(cur) <= 6:
                systems.append(cur)
            cur = []
    if not systems:
        raise ValueError("No tab staves found in the text file (expected lines like 'G|--3--5--|').")
    nstr = len(systems[0])
    from .tuning import parse_tuning

    names = [s[0] for s in systems[0]][::-1]  # tab is written high string first
    try:
        tuning, _ = parse_tuning(" ".join(names), nstr)
    except ValueError:
        tuning = E_STANDARD_4 if nstr == 4 else [23] + E_STANDARD_4
    measures: list[TabMeasure] = []
    pos = 0.0
    for system in systems:
        rows = [s[1] for s in system if len(s[1])][::-1]  # low string first
        if len(rows) != nstr:
            continue
        width = max(len(r) for r in rows)
        rows = [r.ljust(width, "-") for r in rows]
        bar_cols = [c for c in range(width) if all(r[c] == "|" for r in rows if c < len(r))]
        bounds = [0] + bar_cols + [width]
        for a, b in zip(bounds, bounds[1:]):
            if b - a < 3:
                continue
            tm = TabMeasure(start=pos, numerator=4, denominator=4)
            span = b - a - 1
            c = a + 1 if a in bar_cols else a
            while c < b:
                for s, r in enumerate(rows):
                    ch = r[c]
                    if ch.isdigit() or ch in "xX":
                        if ch in "xX":
                            fret, width_chars, dead = 0, 1, True
                        else:
                            m = re.match(r"\d{1,2}", r[c:b])
                            txt = m.group(0)
                            if len(txt) == 2 and int(txt) > 24:
                                txt = txt[0]
                            fret, width_chars, dead = int(txt), len(txt), False
                        frac = (c - (a + 1)) / max(1, span)
                        prev = r[c - 1] if c > 0 else "-"
                        tn = TabNote(start=pos + 4.0 * frac, duration=0.25, string=s, fret=fret, dead=dead)
                        if prev in "hp":
                            tn.hammer_to_next = False
                            if tm.notes and tm.notes[-1].string == s:
                                tm.notes[-1].hammer_to_next = True
                        if prev in "/\\s" and tm.notes and tm.notes[-1].string == s:
                            tm.notes[-1].slide = "legato"
                        nxt = r[c + width_chars] if c + width_chars < len(r) else "-"
                        tn.vibrato = nxt == "~"
                        tm.notes.append(tn)
                        r_skip = width_chars - 1
                        if r_skip:
                            rows[s] = r[:c + 1] + "-" * r_skip + r[c + 1 + r_skip:]
                c += 1
            tm.notes.sort(key=lambda n: n.start)
            for x, y in zip(tm.notes, tm.notes[1:]):
                x.duration = max(0.25, y.start - x.start)
            if tm.notes:
                tm.notes[-1].duration = max(0.25, pos + 4.0 - tm.notes[-1].start)
            measures.append(tm)
            pos += 4.0
    warn("Plain-text tab: rhythms are estimated from spacing and every bar is assumed to be 4/4. "
         "Check the result carefully.")
    return TabSong("", "", 120.0, "text tab", tuning, measures, source=".txt")


# --------------------------------------------------------------------------- entry

def read_tab(path: str | Path, track: int | None = None) -> TabSong:
    path = Path(path)
    ext = path.suffix.lower()
    track_index = None if track is None else track - 1
    if ext in (".gp3", ".gp4", ".gp5"):
        song = _read_gp345(path, track_index)
    elif ext in (".gpx", ".gp"):
        song = _read_gpif(path, track_index)
    else:
        song = _read_text(path)
    nnotes = sum(len(m.notes) for m in song.measures)
    info(f"Tab: {len(song.measures)} measures, {nnotes} notes, track {song.track_name!r}, "
         f"{len(song.tuning)} strings, tempo {song.tempo:.0f}")
    if nnotes == 0:
        raise ValueError("The chosen tab track has no notes. Use --track N to pick the bass track.")
    return song
