"""Review exports: MIDI (exact timing, tempo map from the beat map) and Guitar Pro 5 tab."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .log import warn
from .model import Chart

PPQ = 480


def _beat_positions(chart: Chart) -> tuple[np.ndarray, np.ndarray]:
    bt = np.array([b.time for b in chart.beats])
    return bt, np.arange(len(bt), dtype=float)


def _time_to_beat(chart: Chart, t: float) -> float:
    bt, idx = _beat_positions(chart)
    if t <= bt[0]:
        return (t - bt[0]) / (bt[1] - bt[0])
    if t >= bt[-1]:
        return idx[-1] + (t - bt[-1]) / (bt[-1] - bt[-2])
    return float(np.interp(t, bt, idx))


def write_midi(path: Path, chart: Chart, title: str) -> None:
    import mido

    mid = mido.MidiFile(ticks_per_beat=PPQ)
    tempo = mido.MidiTrack()
    mid.tracks.append(tempo)
    tempo.append(mido.MetaMessage("track_name", name=title, time=0))
    bt = [b.time for b in chart.beats]
    # Initial silence before the first beat is represented as one "beat" at a tempo that
    # spans exactly that time, so MIDI time 0 == audio time 0.
    events = []
    first = bt[0]
    if first > 0.001:
        events.append((0, mido.bpm2tempo(60.0 / first)))
        offset_beats = 1
    else:
        offset_beats = 0
    for i in range(len(bt) - 1):
        events.append(((i + offset_beats) * PPQ, int(round((bt[i + 1] - bt[i]) * 1e6))))
    ts_events = []
    for ts in chart.time_signatures:
        b = int(round(_time_to_beat(chart, ts.time))) + offset_beats
        ts_events.append((b * PPQ, ts))
    msgs = [(t, "tempo", v) for t, v in events] + [(t, "ts", v) for t, v in ts_events]
    msgs.sort(key=lambda x: (x[0], x[1] != "ts"))
    last = 0
    for t, kind, v in msgs:
        if kind == "tempo":
            tempo.append(mido.MetaMessage("set_tempo", tempo=max(1, min(v, 16777215)), time=t - last))
        else:
            tempo.append(mido.MetaMessage("time_signature", numerator=v.numerator, denominator=v.denominator,
                                          time=t - last))
        last = t

    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Bass", time=0))
    track.append(mido.Message("program_change", program=33, channel=0, time=0))

    def tick(t: float) -> int:
        b = _time_to_beat(chart, t)
        b = b + offset_beats if b >= 0 else max(0.0, t / first) if first > 0 else 0.0
        return int(round(b * PPQ))

    evs = []
    for i, n in enumerate(chart.notes):
        on = tick(n.time)
        nxt = chart.notes[i + 1].time if i + 1 < len(chart.notes) else n.time + 0.5
        end_t = n.end if n.sustain > 0 else min(n.time + 0.25, nxt)
        off = max(on + 10, tick(end_t) - 5)
        vel = 70 if n.mute else 100
        evs.append((on, 1, mido.Message("note_on", note=int(n.pitch), velocity=vel, channel=0)))
        evs.append((off, 0, mido.Message("note_off", note=int(n.pitch), velocity=0, channel=0)))
    evs.sort(key=lambda e: (e[0], e[1]))
    last = 0
    for t, _, m in evs:
        track.append(m.copy(time=t - last))
        last = t
    mid.save(str(path))


# --------------------------------------------------------------------------- Guitar Pro 5

_DURS = [  # (ticks at 960/quarter, value, dotted, tuplet)
    (3840, 1, False, None), (2880, 2, True, None), (1920, 2, False, None), (1440, 4, True, None),
    (960, 4, False, None), (720, 8, True, None), (640, 4, False, (3, 2)), (480, 8, False, None),
    (360, 16, True, None), (320, 8, False, (3, 2)), (240, 16, False, None), (160, 16, False, (3, 2)),
    (120, 32, False, None), (80, 32, False, (3, 2)), (60, 64, False, None),
]


def _split(ticks: int) -> list[tuple[int, int, bool, tuple | None]]:
    out = []
    while ticks >= 60:
        for d in _DURS:
            if d[0] <= ticks:
                out.append(d)
                ticks -= d[0]
                break
    return out


def write_gp5(path: Path, chart: Chart, title: str, artist: str, album: str, offsets_tuning: list[int]) -> None:
    import guitarpro as gp

    tuning = chart.tuning_midi[:4] if len(chart.tuning_midi) >= 4 else chart.tuning_midi
    song = gp.Song()
    song.title, song.artist, song.album = title, artist, album
    song.tracks = []
    # Measures from the beat map: [start_beat_index, beats]
    dbs = [i for i, b in enumerate(chart.beats) if b.measure >= 0] + [len(chart.beats)]
    measures = [(dbs[k], dbs[k + 1] - dbs[k]) for k in range(len(dbs) - 1) if dbs[k + 1] > dbs[k]]
    bt = np.array([b.time for b in chart.beats])

    track = gp.Track(song, number=1, name="Bass", strings=[gp.GuitarString(i + 1, p) for i, p in
                                                           enumerate(reversed(tuning))])
    track.channel.instrument = 33
    track.fretCount = 24
    song.tracks.append(track)
    song.measureHeaders = []
    track.measures = []

    def ts_at(t: float):
        cur = None
        for ts in chart.time_signatures:
            if ts.time <= t + 1e-3:
                cur = ts
        return cur

    note_beats = [(_time_to_beat(chart, n.time), i, n) for i, n in enumerate(chart.notes)]
    legato_from = {i - 1 for i, n in enumerate(chart.notes) if i and (n.hammer_on or n.pull_off)}

    start_tick = gp.Duration.quarterTime
    tempo_prev = None
    ni = 0
    for mi, (b0, nb) in enumerate(measures):
        # A beat is a quarter note, or a dotted quarter in compound meters (6/8, 9/8, 12/8).
        ts = ts_at(bt[b0])
        compound = ts is not None and ts.denominator == 8 and ts.numerator % 3 == 0 and ts.numerator == nb * 3
        beat_ticks = 1440 if compound else 960
        header = gp.MeasureHeader(number=mi + 1, start=start_tick)
        header.timeSignature.numerator = nb * 3 if compound else nb
        header.timeSignature.denominator.value = 8 if compound else 4
        song.measureHeaders.append(header)
        measure = gp.Measure(track, header)
        track.measures.append(measure)
        voice = measure.voices[0]
        m_len = nb * beat_ticks
        # Tempo of this measure (GP tempo is always in quarter notes per minute)
        t0 = bt[b0]
        t1 = bt[b0 + nb] if b0 + nb < len(bt) else bt[-1] + (bt[-1] - bt[-2])
        bpm = int(round(60.0 * nb * beat_ticks / 960 / max(1e-3, t1 - t0)))
        notes_here = []
        while ni < len(note_beats) and note_beats[ni][0] < b0 + nb - 1e-6:
            bp, idx, n = note_beats[ni]
            if bp >= b0 - 1e-6:
                local = (bp - b0) * beat_ticks
                if compound:
                    tk = round(local / 240) * 240  # 1/16 grid
                else:
                    straight, trip = round(local / 240) * 240, round(local / 160) * 160
                    tk = straight if abs(straight - local) <= abs(trip - local) + 10 else trip
                if not notes_here or tk > notes_here[-1][0]:
                    notes_here.append((int(min(tk, m_len - 60)), idx, n))
            ni += 1
        pos = 0
        first_beat = True

        def add_beat(ticks_len, note=None, idx=-1):
            nonlocal first_beat
            for d_ticks, value, dotted, tup in _split(ticks_len):
                beat = gp.Beat(voice)
                beat.duration = gp.Duration(value=value, isDotted=dotted,
                                            tuplet=gp.Tuplet(*tup) if tup else gp.Tuplet())
                if first_beat and bpm != tempo_prev:
                    mix = gp.MixTableChange()
                    mix.tempo = gp.MixTableItem(value=bpm, duration=0)
                    beat.effect.mixTableChange = mix
                first_beat = False
                if note is not None:
                    beat.status = gp.BeatStatus.normal
                    s = len(tuning) - note.string  # GP: 1 = highest string
                    gn = gp.Note(beat, value=note.fret, string=s,
                                 type=gp.NoteType.dead if note.mute else gp.NoteType.normal)
                    gn.effect.palmMute = note.palm_mute
                    gn.effect.vibrato = note.vibrato
                    gn.effect.hammer = idx in legato_from
                    if note.slide_to >= 0:
                        gn.effect.slides = [gp.SlideType.shiftSlideTo]
                    beat.notes.append(gn)
                    note = None  # following tied pieces become rests (simpler for review)
                else:
                    beat.status = gp.BeatStatus.rest
                voice.beats.append(beat)

        for k, (tk, idx, n) in enumerate(notes_here):
            if tk > pos:
                add_beat(tk - pos)
                pos = tk
            nxt = notes_here[k + 1][0] if k + 1 < len(notes_here) else m_len
            length = max(60, nxt - tk)
            if n.sustain <= 0:
                length = min(length, 480)
            add_beat(length, n, idx)
            pos = tk + sum(d[0] for d in _split(length))
        if pos < m_len:
            add_beat(m_len - pos)
        if not voice.beats:
            add_beat(m_len)
        tempo_prev = bpm
        start_tick += m_len
        if mi == 0:
            song.tempo = bpm

    try:
        gp.write(song, str(path))
    except Exception as e:  # noqa: BLE001
        warn(f"Could not write the Guitar Pro file ({e}). The MIDI export is still available.")
