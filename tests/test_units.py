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


def test_dlc_keys_are_unique_for_non_latin_names():
    from rs2014conv import project

    a = project.dlc_key("me", "東京事変", "群青日和")
    b = project.dlc_key("me", "東京事変", "閃光少女")
    assert a != b and len(a) >= 5 and a.isalnum()
    assert project.dlc_key("me", "Muse", "Hysteria") == "meMuseHyste"


def test_missed_downbeats_are_filled():
    from rs2014conv.beats import _regularize_bars

    db = np.zeros(24, bool)
    db[[0, 4, 12, 16, 20]] = True  # downbeat at 8 missing
    assert list(np.flatnonzero(_regularize_bars(db))) == [0, 4, 8, 12, 16, 20]


def test_long_sections_get_more_phrases(tmp_path):
    beats = [Beat(1.0 + 0.5 * i, (i // 4 + 1) if i % 4 == 0 else -1) for i in range(4 * 40)]
    notes = [Note(time=3.0 + 0.5 * i, sustain=0, pitch=40, string=2, fret=2) for i in range(120)]
    chart = Chart(beats=beats, notes=notes, tuning_midi=[28, 33, 38, 43], tuning_name="E",
                  time_signatures=[TimeSignatureEvent(1.0, 4, 4)], song_length=90.0)
    chart.sections = sections.finalize([Section("verse", 3.0)], chart.beats, chart.notes)
    meta = dict(title="T", artist="A", album="B", year=2020, title_sort="T", artist_sort="A", album_sort="B")
    out = tmp_path / "p.xml"
    rsxml.write(out, chart, meta, [0] * 6, [(3.0, 1, 4)], False, "bass_base", cent_offset=-31.8, phrase_bars=8)
    root = ET.parse(out).getroot()
    its = root.findall("phraseIterations/phraseIteration")
    assert len(its) >= 5  # COUNT + verse split every 8 bars + END
    assert root.findtext("centOffset") == "-31.8"
    assert len(root.findall("sections/section")) == 2


def test_legato_becomes_hammer_pull_and_slide():
    from rs2014conv.transcribe import apply_legato

    notes = [Note(0.0, 0.2, 38, string=1, fret=5), Note(0.25, 0.2, 36, string=1, fret=3),
             Note(0.5, 0.2, 38, string=1, fret=5), Note(0.75, 0.2, 45, string=1, fret=12)]
    for n in notes[1:]:
        n._legato = True
    assert apply_legato(notes) == 3
    assert notes[1].pull_off and notes[2].hammer_on and notes[2].slide_to == 12


def test_five_string_tab_is_charted_on_four_strings():
    from rs2014conv.pipeline import reduce_to_four_strings

    tun5 = [23, 28, 33, 38, 43]  # B E A D G
    notes = [TabNote(0, 1, 0, 0), TabNote(1, 1, 0, 5), TabNote(2, 1, 4, 2), TabNote(3, 1, 4, 9)]
    song = TabSong("t", "a", 120, "Bass", tun5, [_measure(0, notes)])
    lt = unroll(song)
    al = Alignment(np.arange(0, 10) * 0.5 + 1.0, "test", 0.1, np.zeros(4), 0, [])
    bm = BeatMap(np.arange(0, 20) * 0.5 + 1.0, np.arange(20) % 4 == 0)
    _, chart_notes, _, _ = build_chart_parts(lt, al, bm, 8.0, tun5, 1.0)
    chart_tuning = reduce_to_four_strings(chart_notes, tun5)
    assert chart_tuning == [23, 28, 33, 38]
    fingering.assign(chart_notes, chart_tuning, keep_given=True)
    assert all(0 <= n.string < 4 for n in chart_notes)
    assert [chart_tuning[n.string] + n.fret for n in chart_notes] == [23, 28, 45, 52]
    offsets, _ = tuning.rocksmith_offsets(tun5)
    assert offsets[:4] == [-5, -5, -5, -5]
