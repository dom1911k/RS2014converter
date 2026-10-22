"""Fast unit tests (no models needed):  python -m pytest tests"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rs2014conv import fingering, rsxml, sections, tuning  # noqa: E402
from rs2014conv.beats import BeatMap  # noqa: E402
from rs2014conv.model import Beat, Chart, Note, Section, TimeSignatureEvent  # noqa: E402
from rs2014conv.tab_align import Alignment, build_chart_parts, unroll  # noqa: E402
from rs2014conv.tabs import TabMeasure, TabNote, TabSong  # noqa: E402
from rs2014conv.transcribe import quantize  # noqa: E402


def test_tuning_names():
    assert tuning.parse_tuning("E Standard")[0] == [28, 33, 38, 43]
    assert tuning.parse_tuning("Drop D")[0] == [26, 33, 38, 43]
    assert tuning.parse_tuning("D A D G")[0] == [26, 33, 38, 43]
    assert tuning.parse_tuning("B E A D G")[0] == [23, 28, 33, 38, 43]
    assert tuning.parse_tuning("Eb Standard")[0] == [27, 32, 37, 42]
    offsets, notes = tuning.rocksmith_offsets([23, 28, 33, 38, 43])
    assert offsets[:4] == [-5, -5, -5, -5] and notes


def test_tuning_guess():
    assert tuning.guess_tuning([28, 31, 33, 35, 40] * 5)[1] == "E Standard"
    assert tuning.guess_tuning([26, 26, 33, 38, 40] * 5)[1] == "Drop D"
    assert "5-string" in tuning.guess_tuning([23, 23, 28, 30] * 5)[1]


def _measure(start, notes=(), **kw):
    return TabMeasure(start=start, numerator=4, denominator=4, notes=list(notes), **kw)


def test_repeats_and_endings():
    ms = [_measure(0, repeat_open=True), _measure(4, alternatives=1), _measure(8, alternatives=2, repeat_close=0),
          _measure(12)]
    ms[1].repeat_close = 1
    song = TabSong("t", "a", 120, "Bass", [28, 33, 38, 43], ms)
    assert song.playback() == [0, 1, 0, 2, 3]


def test_dacapo_al_fine():
    ms = [_measure(0), _measure(4, direction="Fine"), _measure(8, jump="DaCapoAlFine")]
    song = TabSong("t", "a", 120, "Bass", [28, 33, 38, 43], ms)
    assert song.playback() == [0, 1, 2, 0, 1]


def test_fingering_prefers_low_positions_and_few_shifts():
    tun = [28, 33, 38, 43]
    notes = [Note(time=i * 0.25, sustain=0, pitch=p) for i, p in enumerate([28, 31, 33, 35, 36, 38, 40, 33])]
    hands = fingering.assign(notes, tun)
    assert all(tun[n.string] + n.fret == n.pitch for n in notes)
    assert max(n.fret for n in notes) <= 5
    assert sum(1 for a, b in zip(hands, hands[1:]) if a != b) <= 2


def test_quantize_follows_drifting_beats():
    times = np.cumsum(np.linspace(0.5, 0.45, 40))
    bm = BeatMap(times, np.arange(40) % 4 == 0)
    target = float(bm.time_at(10.5))
    notes = [Note(time=target + 0.012, sustain=0.1, pitch=40)]
    q = quantize(notes, bm, "1/16", 0.75)
    assert abs(q[0].time - target) < 1e-6


def test_techniques_reach_the_xml(tmp_path):
    notes = [TabNote(0, 1, 0, 3, hammer_to_next=True), TabNote(1, 1, 0, 5, slide="shift"),
             TabNote(2, 1, 0, 7, vibrato=True), TabNote(3, 0.5, 1, 0, dead=True),
             TabNote(3.5, 0.5, 1, 2, palm_mute=True, slap=True)]
    m = [_measure(0, notes, marker="Pre-Chorus")]
    song = TabSong("t", "a", 120, "Bass", [28, 33, 38, 43], m)
    lt = unroll(song)
    beat_times = np.arange(0, 10) * 0.5 + 1.0
    al = Alignment(beat_times, "test", 0.1, np.zeros(4), 0, [])
    bm = BeatMap(np.arange(0, 20) * 0.5 + 1.0, np.arange(20) % 4 == 0)
    beats, chart_notes, secs, ts = build_chart_parts(lt, al, bm, 8.0, song.tuning, 1.0)
    assert secs[0].name == "Pre-Chorus" and sections.normalize(secs[0].name) == "prechorus"
    assert chart_notes[1].hammer_on and chart_notes[1].slide_to == 7 and chart_notes[1].sustain > 0
    assert chart_notes[2].vibrato and chart_notes[3].mute and chart_notes[4].palm_mute
    chart = Chart(beats=beats, notes=chart_notes, tuning_midi=song.tuning, tuning_name="E",
                  time_signatures=ts, song_length=8.0)
    chart.sections = sections.finalize([Section("verse", 1.0)], chart.beats, chart.notes)
    hands = fingering.assign(chart.notes, song.tuning, keep_given=True)
    meta = dict(title="T", artist="A", album="B", year=2020, title_sort="T", artist_sort="A", album_sort="B")
    out = tmp_path / "a.xml"
    rsxml.write(out, chart, meta, [0] * 6, fingering.anchors(chart.notes, hands), False, "bass_base")
    root = ET.parse(out).getroot()
    xn = root.findall(".//level/notes/note")
    assert len(xn) == 5
    assert xn[1].get("hammerOn") == "1" and xn[1].get("slideTo") == "7"
    assert root.find("phrases/phrase").get("name") == "COUNT"
    assert root.findall("sections/section")[-1].get("name") == "noguitar"
    assert root.find("levels").get("count") == "1"


def test_section_names_are_valid():
    for name in ["Verse 1", "CHORUS", "Intro", "Guitar Solo", "Bridge", "Interlude", "A", "Outro", "Pre Chorus"]:
        assert sections.normalize(name) in sections.VALID
