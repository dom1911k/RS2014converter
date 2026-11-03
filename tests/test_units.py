"""Fast unit tests (no models needed):  python -m pytest tests"""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest

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


def test_beat_glitch_repaired_but_tempo_change_kept():
    from rs2014conv.beats import _repair_glitches

    steady = np.arange(0, 10, 0.476)
    glitch = np.concatenate([steady, 10 + np.arange(0, 2.5, 0.36), 12.9 + np.arange(0, 8, 0.476)])
    fixed = _repair_glitches(glitch)
    assert np.abs(np.diff(fixed) - 0.476).max() < 0.06
    change = np.concatenate([np.arange(0, 20, 0.5), 20 + np.arange(1, 40) * 0.4])
    assert np.allclose(_repair_glitches(change), change)


def test_japanese_titles(tmp_path):
    from rs2014conv import exports, project
    from rs2014conv.text import romanize

    assert romanize("自由のショート") == "Jiyuu No Shooto"
    assert project.sort_value("自由のショート") == "Jiyuu No Shooto"
    assert project.dlc_key("me", "haku.", "自由のショート") == "mehakuJiyuu"
    beats = [Beat(1.0 + 0.5 * i, (i // 4 + 1) if i % 4 == 0 else -1) for i in range(16)]
    chart = Chart(beats=beats, notes=[Note(2.0, 0, 40, string=2, fret=2)], tuning_midi=[28, 33, 38, 43],
                  tuning_name="E", time_signatures=[TimeSignatureEvent(1.0, 4, 4)], song_length=9.0)
    exports.write_midi(tmp_path / "a.mid", chart, "自由のショート")  # used to crash (latin-1)
    exports.write_gp5(tmp_path / "a.gp5", chart, "自由のショート", "haku.", "", [0] * 6)
    assert (tmp_path / "a.mid").exists() and (tmp_path / "a.gp5").exists()


def test_intro_and_breaks_become_sections():
    beats = [Beat(0.5 + 0.5 * i, (i // 4 + 1) if i % 4 == 0 else -1) for i in range(200)]
    notes = [Note(16.5 + 0.5 * k, 0, 40, string=2, fret=2) for k in range(64)] + \
            [Note(52.5 + 0.5 * k, 0, 40, string=2, fret=2) for k in range(56)]
    out = sections.finalize([Section("intro", 0.5), Section("verse", 16.5), Section("bridge", 50.5),
                             Section("chorus", 60.5)], beats, notes)
    assert [(s.name, s.time) for s in out] == [("intro", 2.5), ("verse", 16.5), ("noguitar", 48.5),
                                               ("bridge", 52.5), ("chorus", 60.5), ("noguitar", 80.5)]
    plain = sections.finalize([], beats, notes)
    assert [s.name for s in plain] == ["intro", "noguitar", "riff", "noguitar"]


def test_semitone_check_fixes_a_flat_reading():
    from rs2014conv.transcribe import SR, verify_semitones

    t = np.arange(int(0.5 * SR)) / SR
    f = 65.41  # C2
    y = sum(np.sin(2 * np.pi * f * h * t) / h for h in range(1, 6)).astype(np.float32)
    notes = [Note(0.0, 0.5, 35)]  # read as B1, like CREPE did on a real song
    assert verify_semitones(notes, y) == 1 and notes[0].pitch == 36


def test_tab_blocks_follow_the_bass_pattern():
    from rs2014conv.tab_align import tab_block_starts

    def bar(fret):
        return [TabNote(k * 0.5, 0.5, 0, fret) for k in range(8)]

    pattern = [3, 3, 5, 5, 7, 7, 8, 8]  # an 8-bar cycle, as in many songs
    frets = [None] * 2 + pattern + pattern + [None, None] + pattern + [10, 12, 10, 12] + pattern
    ms = []
    for i, f in enumerate(frets):
        m = _measure(4.0 * i, [] if f is None else bar(f))
        for n in m.notes:
            n.start += m.start
        ms.append(m)
    lt = unroll(TabSong("t", "a", 120, "Bass", [28, 33, 38, 43], ms))
    starts = tab_block_starts(lt, np.arange(lt.total_beats + 1, dtype=float))
    bars = [int(t // 4) + 1 for t in starts]
    assert bars == [3, 11, 21, 29, 33]


def test_dtw_skips_whole_bars():
    from rs2014conv.tab_align import _dtw

    rng = np.random.default_rng(0)
    tab = rng.random((16, 6))  # 4 bars of 4 beats
    audio = np.vstack([tab[:8], rng.random((4, 6)), tab[8:]])  # recording has an extra bar after bar 2
    C = 1 - (tab / np.linalg.norm(tab, axis=1, keepdims=True)) @ (audio / np.linalg.norm(audio, axis=1, keepdims=True)).T
    skip = np.where(np.arange(16) % 4 == 3, 0.35, 2.0)
    path, _ = _dtw(C, skip)
    mapping = {i: j for i, j in path}
    assert mapping[7] == 7 and mapping[8] == 12  # the skip happens exactly at the bar line


def test_bass_trainer_chart(tmp_path):
    """A Bass Trainer chart (.btchart.json from the browser extension) reads like a tab."""
    import json

    from rs2014conv.tabs import is_btchart, read_tab

    # Bar 1: 4/4 at 120 BPM (2 s), bar 2: 6/8 (dotted-quarter = 60 BPM -> 3 s).
    bars = [{"i": 0, "t": 0.0, "end": 2.0, "measure": 0, "sig": [4, 4]},
            {"i": 1, "t": 2.0, "end": 5.0, "measure": 1, "sig": [6, 8]}]
    notes = [
        {"t": 0.0, "dur": 0.25, "string": 0, "fret": 5, "flags": {"palmMute": True}},
        {"t": 0.25, "dur": 0.25, "string": 0, "fret": 7, "flags": {"hopo": True}},          # hammer-on
        {"t": 0.5, "dur": 0.5, "string": 1, "fret": 2, "flags": {"slide": {"type": "legato", "toFret": 4}}},
        {"t": 1.0, "dur": 0.5, "string": 1, "fret": 4, "flags": {"slideTarget": True}},
        {"t": 1.0 + 1 / 3, "dur": 0.1, "string": 2, "fret": 0, "flags": {"dead": True}},  # triplet position
        {"t": 2.0, "dur": 1.0, "string": 3, "fret": 9, "flags": {"harmonic": True, "vibrato": True}},
    ]
    chart = {"format": "bass-chart", "version": 1, "meta": {"title": "Test", "artist": "Band", "track": "Bass"},
             "instrument": {"strings": 4, "tuning": [26, 33, 38, 43]}, "duration": 5.0, "bars": bars,
             "sections": [{"name": "Intro", "t": 0.0}, {"name": "Verse", "t": 2.0}], "notes": notes}
    path = tmp_path / "Band - Test.btchart.json"
    path.write_text(json.dumps(chart), encoding="utf-8")
    assert is_btchart(path) and not is_btchart(tmp_path / "missing.json")

    song = read_tab(path)
    assert (song.title, song.artist, song.tuning) == ("Test", "Band", [26, 33, 38, 43])
    m1, m2 = song.measures
    assert (m1.start, m1.length, m1.marker, round(m1.tempo)) == (0.0, 4.0, "Intro", 120)
    assert (m2.start, m2.numerator, m2.denominator, m2.marker) == (4.0, 6, 8, "Verse")
    assert m2.beats == 2 and round(m2.tempo) == 60  # quarter BPM: 3 quarters in 3 s
    n = m1.notes
    assert [x.start for x in n] == [0.0, 0.5, 1.0, 2.0, 2 + 2 / 3]
    assert n[0].palm_mute and n[0].hammer_to_next and not n[1].hammer_to_next
    assert n[2].slide == "legato" and n[3].fret == 4
    assert n[4].dead and m2.notes[0].harmonic and m2.notes[0].vibrato and m2.notes[0].duration == 1.0
    lt = unroll(song)
    assert len(lt.measures) == 2 and lt.total_beats == 6 and len(lt.notes) == 6


def test_chart_timing_offset_drift_and_repeats():
    """Songsterr timing: offset and drift are found from the attacks, also in a song that
    repeats one rhythm bar after bar (where one bar off fits the attacks just as well)."""
    from rs2014conv.chart_timing import fit_timing

    rng = np.random.default_rng(3)
    # 200 s of steady eighth notes (a very repetitive song) with a few gaps.
    notes = np.arange(0, 200, 0.4347826)
    notes = notes[rng.random(len(notes)) > 0.05]
    true_off, true_drift = 11.2, -0.0003
    attacks = notes * (1 + true_drift) + true_off + rng.normal(0, 0.006, len(notes))
    fit = fit_timing(notes, attacks)
    assert fit.match > 0.97
    # Rhythm alone can't tell the bars apart, so the fit may be any whole number of notes away...
    k = round((fit.offset - true_off) / 0.4347826)
    assert abs(fit.offset - k * 0.4347826 * (1 + true_drift) - true_off) < 0.02
    assert abs(fit.drift - true_drift) < 0.0001
    # ...but the pitches can: with a pitch score the true offset wins.
    def pitch(f):
        return 1.0 - min(1.0, abs(f.offset - true_off) * 2)
    fit = fit_timing(notes, attacks, pitch_score=pitch)
    assert abs(fit.offset - true_off) < 0.01 and fit.runner_up is not None
    # An offset from the player only needs refining.
    assert abs(fit_timing(notes, attacks, center=11.25, pitch_score=pitch).offset - true_off) < 0.01
    # A different recording (other groove): few notes land on attacks.
    other = np.sort(notes + rng.normal(0, 0.15, len(notes)) + 3.0)
    assert fit_timing(notes, other).match < 0.6


def test_bass_trainer_host(tmp_path):
    """The Chrome link: receives a song folder in messages, builds it, reports the result."""
    import base64
    import io
    import time

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "native_host"))
    import bass_trainer_host as bt

    # Message framing round-trip.
    buf = io.BytesIO()
    bt.send({"type": "x", "s": "é"}, buf)
    buf.seek(0)
    assert bt.read_message(buf) == {"type": "x", "s": "é"} and bt.read_message(buf) is None
    # Only plain names inside the songs folder.
    assert bt.safe_name("..\\..\\Windows\\evil.bat") == "evil.bat"
    assert bt.safe_name("A/B: c?") == "B_ c_"
    with pytest.raises(ValueError):
        bt.safe_name("..")

    # A stand-in for make_cdlc: prints progress, writes output/<song>/report.txt and a .psarc.
    fake = tmp_path / "fake_make.py"
    fake.write_text(
        "import sys, pathlib\n"
        "song = pathlib.Path(sys.argv[1]); out = song / 'output' / 'Band - Song'; out.mkdir(parents=True)\n"
        "print('== Reading tab'); print('  Tab: 2 measures')\n"
        "(out / 'report.txt').write_text('Band - Song\\nPLEASE CHECK:\\n  * Listen at 1:02.\\n\\nrest\\n')\n"
        "(out / 'Band_Song_v1_p.psarc').write_bytes(b'psarc')\n", encoding="utf-8")
    sent = []
    host = bt.Host(write=sent.append, build_cmd=[sys.executable, str(fake)])
    host.cfg["bass_trainer"] = {"songs_folder": str(tmp_path / "songs")}
    host.cfg["packaging"]["copy_to_folder"] = ""
    host.handle({"type": "hello"})
    assert sent[-1]["type"] == "hello" and sent[-1]["songsDir"] == str(tmp_path / "songs")
    audio = b"RIFF" + bytes(range(256)) * 40
    host.handle({"type": "begin", "folder": "Band - Song", "files": [{"name": "song.yaml", "text": "title: Song\n"},
                                                                      {"name": "../x.btchart.json", "text": "{}"}],
                 "audio": {"name": "song.wav", "size": len(audio)}})
    assert sent[-1]["type"] == "ready"
    for k in range(0, len(audio), 4000):
        host.handle({"type": "chunk", "data": base64.b64encode(audio[k:k + 4000]).decode()})
    host.handle({"type": "end"})
    folder = tmp_path / "songs" / "Band - Song"
    assert sent[-1] == {"type": "saved", "dir": str(folder)}
    assert (folder / "song.wav").read_bytes() == audio and (folder / "x.btchart.json").exists()
    host.handle({"type": "build"})
    for _ in range(100):
        if sent[-1]["type"] == "done":
            break
        time.sleep(0.05)
    done = sent[-1]
    assert done["ok"] and done["psarc"].endswith("Band_Song_v1_p.psarc") and done["checks"] == ["Listen at 1:02."]
    assert any(m == {"type": "step", "text": "Reading tab"} for m in sent)
    # A short upload is caught.
    host.handle({"type": "begin", "folder": "Other", "files": [], "audio": {"name": "a.wav", "size": 10}})
    host.handle({"type": "chunk", "data": base64.b64encode(b"abc").decode()})
    with pytest.raises(ValueError):
        host.handle({"type": "end"})


def test_desktop_and_maker(tmp_path, monkeypatch):
    """Installer helpers (Steam libraries, dlc folder, icon, your settings) and the CDLC Maker's logic."""
    import yaml

    from rs2014conv import config, desktop, maker

    # Steam: the dlc folder is found in a second library listed in libraryfolders.vdf.
    steam, lib2 = tmp_path / "Steam", tmp_path / "Games" / "SteamLibrary"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0"\n {\n  "path"  "%s"\n }\n "1"\n {\n  "path"  "%s"\n }\n}\n'
        % (str(steam).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\")), encoding="utf-8")
    assert desktop.find_rocksmith_dlc([steam]) is None
    dlc = lib2 / "steamapps" / "common" / "Rocksmith2014" / "dlc"
    dlc.mkdir(parents=True)
    assert desktop.find_rocksmith_dlc([steam]) == dlc
    assert desktop.make_icon(tmp_path / "icon.ico").stat().st_size > 1000

    # Your settings: a separate file, read after config.yaml, kept by updates.
    monkeypatch.setattr(config, "USER_SETTINGS", tmp_path / "settings.yaml")
    config.save_user_setting("packaging", "copy_to_folder", str(dlc))
    assert config.load(None)["packaging"]["copy_to_folder"] == str(dlc)
    assert yaml.safe_load((tmp_path / "settings.yaml").read_text(encoding="utf-8"))["packaging"]["copy_to_folder"] == str(dlc)

    # A song from a dropped audio file -> saved as a song folder -> loaded again.
    audio = tmp_path / "Muse - Hysteria.flac"
    audio.write_bytes(b"fLaC")
    s = maker.load_song(audio)
    assert (s.artist, s.title) == ("Muse", "Hysteria") and s.missing() == []
    folder = maker.save_song(s, base=tmp_path / "songs")
    assert folder.name == "Muse - Hysteria"
    again = maker.load_song(folder)
    assert (again.title, again.artist, again.path(again.audio)) == ("Hysteria", "Muse", audio)
    # A kit folder from the Bass Trainer extension: audio and chart inside, extra keys kept.
    kit = tmp_path / "songs" / "Band - Song"
    kit.mkdir()
    (kit / "song.yaml").write_text('title: "Song"\nartist: "Band"\ntab: "Band - Song.btchart.json"\n'
                                   'audio: "song.wav"\nchart_offset: 2.371\n', encoding="utf-8")
    (kit / "Band - Song.btchart.json").write_text('{"format": "bass-chart"}', encoding="utf-8")
    (kit / "song.wav").write_bytes(b"RIFF")
    k = maker.load_song(kit)
    assert k.extra == {"chart_offset": 2.371} and k.missing() == []
    maker.save_song(k)
    assert "chart_offset: 2.371" in (kit / "song.yaml").read_text(encoding="utf-8")
    # Results: newest output folder, the report's checks, the check mix.
    out = kit / "output" / "Band - Song"
    (out / "review").mkdir(parents=True)
    (out / "report.txt").write_text("x\nPLEASE CHECK:\n  * Listen at 1:02.\n  * Tuning guessed.\n\nmore\n", encoding="utf-8")
    (out / "Band_Song_v1_p.psarc").write_bytes(b"p")
    (out / "review" / "check_mix.wav").write_bytes(b"w")
    r = maker.latest_result(kit)
    assert r.checks == ["Listen at 1:02.", "Tuning guessed."] and r.psarc.name == "Band_Song_v1_p.psarc" and r.check_mix
    names = [s.title for _, s, _ in maker.list_songs(tmp_path / "songs")]
    assert set(names) == {"Song", "Hysteria"}
    assert maker.Song(title="T", artist="A", audio=str(tmp_path / "gone.mp3")).missing() == ["the audio file"]
