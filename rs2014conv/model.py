"""Core data types shared by every stage of the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Note:
    """One bass note. Times are in seconds of the original audio."""

    time: float
    sustain: float  # 0 = short note (Rocksmith shows no tail)
    pitch: int  # MIDI note number (concert pitch)
    string: int = -1  # 0 = lowest string
    fret: int = -1
    confidence: float = 1.0
    # Techniques (mostly filled from tabs)
    hammer_on: bool = False
    pull_off: bool = False
    slide_to: int = -1  # target fret of a pitched slide
    slide_unpitch_to: int = -1
    mute: bool = False  # dead note (x)
    palm_mute: bool = False
    harmonic: bool = False
    accent: bool = False
    vibrato: bool = False
    slap: bool = False
    pop: bool = False
    tap: bool = False
    tremolo: bool = False
    bend: float = 0.0  # semitones
    link_next: bool = False
    tie_from_previous: bool = False  # internal: merged with previous note

    @property
    def end(self) -> float:
        return self.time + self.sustain


@dataclass
class Beat:
    time: float
    measure: int  # measure number if this beat is a downbeat, otherwise -1


@dataclass
class Section:
    name: str  # Rocksmith section name, e.g. "verse"
    time: float


@dataclass
class TimeSignatureEvent:
    time: float
    numerator: int
    denominator: int


@dataclass
class Chart:
    beats: list[Beat]
    notes: list[Note]
    tuning_midi: list[int]  # open string pitches, low to high
    tuning_name: str
    sections: list[Section] = field(default_factory=list)
    time_signatures: list[TimeSignatureEvent] = field(default_factory=list)
    song_length: float = 0.0
    warnings: list[str] = field(default_factory=list)
    flagged: list[tuple[float, float, str]] = field(default_factory=list)  # (start, end, reason)

    @property
    def string_count(self) -> int:
        return len(self.tuning_midi)
