"""The whole pipeline: inputs -> chart -> Rocksmith files -> review files -> .psarc."""

from __future__ import annotations

import datetime as _dt
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import audio_io, beats as beats_mod, checkmix, cover, exports, fingering, project, rsxml, sections, tuning
from .config import ROOT
from .log import collected_warnings, info, step, warn
from .model import Beat, Chart, Note, Section, TimeSignatureEvent

SR = 44100
TONE_KEY = "bass_base"


@dataclass
class SongInputs:
    audio: Path
    title: str
    artist: str
    album: str = ""
    year: int = 0
    tab: Path | None = None
    track: int | None = None
    stem: Path | None = None
    tuning: str | None = None
    strings: int = 4
    cover: Path | None = None
    grid: str | None = None
    bass_picked: bool | None = None
    version: str = "1"
    output_dir: Path | None = None


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)

    def add(self, text: str = "") -> None:
        self.lines.append(text)


def _fmt(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:06.3f}"


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or "song"


# --------------------------------------------------------------------------- helpers

def beats_from_map(bm: beats_mod.BeatMap) -> tuple[list[Beat], list[TimeSignatureEvent]]:
    out = [Beat(float(t), m) for t, m in zip(bm.times, bm.measure_numbers())]
    ts = []
    for start, n in bm.beats_per_measure():
        if not ts or ts[-1].numerator != n:
            ts.append(TimeSignatureEvent(float(bm.times[start]), n, 4))
    return out, ts


def pad_start(chart: Chart, pad: float) -> None:
    """Shift everything by `pad` seconds (silence added in front of the audio) and add beats."""
    for b in chart.beats:
        b.time += pad
    for n in chart.notes:
        n.time += pad
    for s in chart.sections:
        s.time += pad
    for ts in chart.time_signatures:
        ts.time += pad
    chart.flagged = [(a + pad, b + pad, r) for a, b, r in chart.flagged]
    first = chart.beats[0].time
    ibi = chart.beats[1].time - chart.beats[0].time
    dbs = [i for i, b in enumerate(chart.beats) if b.measure >= 0]
    bpm = dbs[1] - dbs[0] if len(dbs) > 1 else 4
    new = []
    t = first - ibi
    while t > 0.02:
        new.append(t)
        t -= ibi
    new = new[::-1]
    pre = [Beat(t, 0 if (len(new) - k) % bpm == 0 or k == 0 else -1) for k, t in enumerate(new)]
    chart.beats = pre + chart.beats
    m = 0
    for b in chart.beats:
        if b.measure >= 0:
            m += 1
            b.measure = m


def reduce_to_four_strings(notes: list[Note], tun: list[int]) -> list[int]:
    """Rocksmith basses have 4 strings. Keep the lowest 4 and re-finger the rest."""
    if len(tun) <= 4:
        return tun
    chart_tuning = tun[:4]
    moved = 0
    for n in notes:
        if n.string >= 4:
            n.string, n.fret = -1, -1
            moved += 1
    if moved:
        info(f"{moved} note(s) on the top string(s) were moved onto the lower four strings")
    return chart_tuning


def _check_notes(chart: Chart) -> None:
    notes = sorted([n for n in chart.notes if 0 <= n.time < chart.song_length - 0.05], key=lambda n: n.time)
    out = []
    for n in notes:
        if out and abs(n.time - out[-1].time) < 0.005:
            continue
        if n.end > chart.song_length - 0.05:
            n.sustain = max(0.0, chart.song_length - 0.1 - n.time)
        if not (0 <= n.string < 4 and 0 <= n.fret <= 24):
            warn(f"Dropped unplayable note at {_fmt(n.time)} (pitch {n.pitch}).")
            continue
        out.append(n)
    for a, b in zip(out, out[1:]):
        if a.sustain > 0 and a.end > b.time and a.string == b.string:
            a.sustain = max(0.0, b.time - a.time - 0.02)
            if a.sustain < 0.1 and not a.link_next:
                a.sustain = 0.0
        if a.link_next and (a.string != b.string):
            a.link_next = False
    chart.notes = out


def find_packager(cfg: dict) -> list[str] | None:
    p = cfg["packaging"].get("packager_path")
    candidates = [Path(p)] if p else []
    for base in (ROOT / "packager" / "publish", ROOT / "packager" / "bin" / "Release" / "net10.0"):
        candidates += [base / "RsPackager.exe", base / "RsPackager"]
    for c in candidates:
        if c.exists():
            return [str(c)]
    dll = ROOT / "packager" / "bin" / "Release" / "net10.0" / "RsPackager.dll"
    if dll.exists() and shutil.which("dotnet"):
        return [shutil.which("dotnet"), str(dll)]
    return None


# --------------------------------------------------------------------------- main

def run(inp: SongInputs, cfg: dict, package: bool = True) -> Path:
    report = Report()
    device = cfg["device"]
    cache = Path(cfg["cache_dir"])
    out_root = inp.output_dir or Path(cfg["output_dir"] or (inp.audio.parent / "output"))
    out_dir = out_root / _safe(f"{inp.artist} - {inp.title}")
    proj_dir = out_dir / "project"
    review_dir = out_dir / "review"
    for d in (proj_dir, review_dir, cache):
        d.mkdir(parents=True, exist_ok=True)
    bass_picked = cfg["bass_picked"] if inp.bass_picked is None else inp.bass_picked

    report.add(f"make_cdlc report - {inp.artist} - {inp.title}")
    report.add(f"Created {_dt.datetime.now():%Y-%m-%d %H:%M}")
    report.add(f"Audio: {inp.audio}")
    report.add(f"Tab: {inp.tab or '(none - transcribed from audio)'}")
    report.add("")

    step("Loading audio")
    mix = audio_io.load(inp.audio, sr=SR)
    song_length = mix.shape[1] / SR
    first_sound = beats_mod.first_sound_time(mix, SR)
    info(f"{song_length:.1f} s, first sound at {first_sound:.2f} s")

    step("Bass stem")
    if inp.stem:
        stem_path = inp.stem
        info(f"Using the given bass stem: {stem_path.name}")
    else:
        from .separation import separate_bass

        stem_path = separate_bass(inp.audio, cache, cfg["separation"]["model"], device,
                                  int(cfg["separation"]["shifts"]))

    step("Beat map")
    bm = beats_mod.detect(inp.audio, cache, cfg["beats"]["tracker"], device, song_length,
                          cfg["beats"].get("checkpoint", "final0"))
    report.add(f"Beat tracker: {bm.source}; median tempo {bm.average_tempo():.1f} BPM "
               f"(range {np.percentile(bm.tempo_curve(), 5):.1f}-{np.percentile(bm.tempo_curve(), 95):.1f})")

    chart_tuning: list[int]
    tuning_note = ""
    flagged: list[tuple[float, float, str]] = []
    marker_sections: list[Section] = []
    if inp.tab:
        from . import tab_align, tabs

        step("Reading tab")
        song = tabs.read_tab(inp.tab, inp.track)
        tab_tuning = song.tuning
        if inp.tuning:
            tun, tname = tuning.parse_tuning(inp.tuning, len(tab_tuning))
            if len(tun) != len(tab_tuning):
                warn(f"--tuning has {len(tun)} strings but the tab has {len(tab_tuning)}; using the tab's string count.")
                tun = tab_tuning
            tuning_note = f"{tname} (given)"
        else:
            tun = tab_tuning
            tuning_note = f"{tuning.describe(tun)} (from the tab)"
        step("Aligning tab to audio")
        lt = tab_align.unroll(song)
        al = tab_align.align(lt, tun, stem_path, bm, try_shifts=cfg["tab"]["detect_tuning_shift"] and not inp.tuning)
        if al.pitch_shift:
            tun = [p + al.pitch_shift for p in tun]
            tuning_note = f"{tuning.describe(tun)} (tab tuning shifted {al.pitch_shift:+d} to match the recording)"
        beat_list, notes, marker_sections, tsigs = tab_align.build_chart_parts(
            lt, al, bm, song_length, tun, float(cfg["tab"]["min_sustain_beats"]))
        flagged = al.flagged
        report.add(f"Tab alignment: grid '{al.grid_name}', match quality {1 - al.cost:.2f}")
        if cfg["tab"].get("snap_to_onsets", False):
            moved, med_ms = tab_align.snap_to_onsets(notes, stem_path, float(cfg["tab"].get("snap_tolerance", 0.04)))
            info(f"{moved} tab note(s) nudged onto the attacks in the recording (median {med_ms:.0f} ms)")
            report.add(f"Tab notes nudged onto the recording's attacks: {moved} (median {med_ms:.0f} ms)")
        chart_tuning = reduce_to_four_strings(notes, tun)
        keep_given = True
    else:
        from .transcribe import TranscribeConfig, transcribe

        step("Transcribing bass")
        tcfg = TranscribeConfig(**{k: v for k, v in cfg["transcription"].items()
                                   if k in TranscribeConfig.__dataclass_fields__})
        tcfg.device = device
        if inp.grid:
            tcfg.grid = inp.grid
        if inp.tuning:  # a known tuning gives the real playable range for the octave checks
            tun, tname = tuning.parse_tuning(inp.tuning, inp.strings)
            tcfg.lowest_pitch = tun[0]
            tcfg.highest_pitch = tun[min(3, len(tun) - 1)] + 24
        notes, stats = transcribe(stem_path, bm, tcfg, cache)
        if inp.tuning:
            tuning_note = f"{tname} (given)"
        else:
            tun, tname, why = tuning.guess_tuning([n.pitch for n in notes], inp.strings)
            tuning_note = f"{tname} (GUESSED: {why}) - pass --tuning if this is wrong"
            info(f"Tuning guess: {tname} ({why})")
        beat_list, tsigs = beats_from_map(bm)
        report.add(f"Transcription: {stats}")
        chart_tuning = tun[:4]
        keep_given = False

    step("Fingering")
    fcfg = fingering.FingeringConfig(**{k: v for k, v in cfg["fingering"].items()
                                        if k in fingering.FingeringConfig.__dataclass_fields__})
    notes.sort(key=lambda n: n.time)
    hands = fingering.assign(notes, chart_tuning, fcfg, keep_given=keep_given)
    moved_oct = sum(1 for n in notes if getattr(n, "_octave_moved", False))
    if moved_oct:
        warn(f"{moved_oct} note(s) were out of range for the tuning and were moved by an octave.")
    if not inp.tab:
        from .transcribe import apply_legato

        legato = apply_legato(notes)
        if legato:
            info(f"{legato} legato note(s) marked as hammer-on / pull-off / slide")
    shifts = sum(1 for a, b in zip(hands, hands[1:]) if a != b)
    info(f"{len(notes)} notes, {shifts} hand position changes, highest fret {max((n.fret for n in notes), default=0)}")

    chart = Chart(beats=beat_list, notes=notes, tuning_midi=chart_tuning, tuning_name=tuning_note,
                  time_signatures=tsigs, song_length=song_length, flagged=flagged)

    # Lead-in: Rocksmith needs an empty count-in before the first note.
    first_note = min((n.time for n in notes), default=song_length)
    lead = float(cfg["lead_in_seconds"])
    pad = 0.0
    beats_before = sum(1 for b in chart.beats if b.time < first_note - 0.01)
    if first_note < lead or beats_before < 2:
        pad = max(lead - first_note, 0.0)
        ibi = chart.beats[1].time - chart.beats[0].time
        if beats_before + int(pad / ibi) < 2:
            pad += 2 * ibi
        pad = round(pad, 3)
        info(f"First note at {first_note:.2f} s: adding {pad:.2f} s of silence at the start for a count-in")
        pad_start(chart, pad)
        mix = np.concatenate([np.zeros((mix.shape[0], int(round(pad * SR))), np.float32), mix], axis=1)
        chart.song_length = mix.shape[1] / SR
        first_note += pad
    for s in marker_sections:
        s.time += pad

    step("Sections")
    if marker_sections:
        secs = sections.from_markers(marker_sections)
        info("Sections from tab markers: " + ", ".join(f"{s.name}@{s.time:.1f}s" for s in secs))
    elif cfg["sections"]["auto_detect"]:
        try:
            tmp = cache / "_padded_mix.wav"
            audio_io.save_wav(tmp, mix, SR)
            secs = sections.auto_detect(tmp, beats_mod.BeatMap(np.array([b.time for b in chart.beats]),
                                                                 np.array([b.measure >= 0 for b in chart.beats])),
                                        chart.notes, int(cfg["sections"]["min_bars"]))
        except Exception as e:  # noqa: BLE001
            warn(f"Section detection failed ({e}); using generic sections.")
            secs = []
    else:
        secs = []
    if not secs:
        dbs = [b.time for b in chart.beats if b.measure >= 0]
        secs = [Section("riff", t) for t in dbs[::8]]
    chart.sections = sections.finalize(secs, chart.beats, chart.notes)
    _check_notes(chart)
    hands = fingering.assign(chart.notes, chart_tuning, fcfg, keep_given=True)
    anchors = fingering.anchors(chart.notes, hands)

    offsets, tnotes = tuning.rocksmith_offsets(chart_tuning)
    for t in tnotes:
        warn(t)

    # Tuning reference (A=440 or not): measured on the bass stem unless set in config.yaml.
    tp = cfg.get("tuning_pitch", "auto")
    if str(tp).lower() == "auto":
        cents = tuning.estimate_reference_cents(stem_path)
        info(f"Tuning reference: {cents:+.0f} cents from A=440 (A={tuning.cents_to_pitch(cents):.1f} Hz)")
        if abs(cents) < 10:
            cents = 0.0
    else:
        cents = 1200 * float(np.log2(float(tp) / 440.0))
    tuning_pitch = tuning.cents_to_pitch(cents)
    if cents:
        report.add(f"Tuning reference: A={tuning_pitch:.1f} Hz ({cents:+.0f} cents) - the in-game tuner uses this")

    step("Writing Rocksmith files")
    meta = {
        "title": inp.title, "artist": inp.artist, "album": inp.album or inp.title,
        "year": inp.year or _dt.date.today().year, "version": inp.version,
    }
    meta.update(title_sort=project.sort_value(meta["title"]), artist_sort=project.sort_value(meta["artist"]),
                album_sort=project.sort_value(meta["album"]))
    xml_path = proj_dir / "arr_bass_RS2.xml"
    rsxml.write(xml_path, chart, meta, offsets, anchors, bass_picked, TONE_KEY, cent_offset=cents,
                phrase_bars=int(cfg["sections"].get("phrase_bars", 8)))
    (proj_dir / "auto_showlights.xml").unlink(missing_ok=True)  # regenerated from the new chart

    audio_path = proj_dir / "audio.wav"
    tmp_audio = proj_dir / "audio.tmp.wav"
    audio_io.save_wav(tmp_audio, mix, SR)
    if audio_path.exists() and audio_io.file_hash(audio_path) == audio_io.file_hash(tmp_audio):
        tmp_audio.unlink()  # unchanged: keeps an existing .wem conversion valid
    else:
        tmp_audio.replace(audio_path)
        for old in (proj_dir / "audio.wem", proj_dir / "audio_preview.wav", proj_dir / "audio_preview.wem"):
            old.unlink(missing_ok=True)
    cover_path = proj_dir / "cover.png"
    if inp.cover:
        from PIL import Image

        from PIL import ImageOps

        ImageOps.fit(Image.open(inp.cover).convert("RGB"), (512, 512), Image.LANCZOS).save(cover_path)
    elif not cover_path.exists():
        cover.make_cover(cover_path, inp.title, inp.artist)

    chorus = next((s.time for s in chart.sections if s.name == "chorus"), None)
    preview_start = chorus if chorus is not None else chart.song_length / 2 - 14
    preview_start = max(0.0, min(preview_start, chart.song_length - 30))
    ids = project.load_ids(proj_dir / "ids.json", project.dlc_key(cfg["charter_name"], inp.artist, inp.title))
    rs2dlc = proj_dir / f"{_safe(inp.artist)}_{_safe(inp.title)}.rs2dlc"
    project.write(rs2dlc, ids=ids, meta=meta, audio="audio.wav", preview="audio_preview.wav",
                  preview_start=preview_start, cover="cover.png", xml=xml_path.name, offsets=offsets,
                  bass_pick=bass_picked, tone_key=TONE_KEY, author=cfg["charter_name"], tuning_pitch=tuning_pitch)
    info(f"Arrangement XML: {xml_path}")
    info(f"DLC Builder project: {rs2dlc}")
    eof_path = _write_eof(proj_dir, audio_path, xml_path, cfg)

    step("Review files")
    exports.write_midi(review_dir / "chart.mid", chart, inp.title)
    exports.write_gp5(review_dir / "chart.gp5", chart, inp.title, inp.artist, meta["album"], offsets)
    stem_audio = audio_io.load(stem_path, sr=SR)
    if pad:
        stem_audio = np.concatenate([np.zeros((stem_audio.shape[0], int(round(pad * SR))), np.float32), stem_audio], 1)
    cm = cfg["check_mix"]
    main, split = checkmix.render(chart, mix, stem_audio if cm["split_file"] else None, SR, cm["clicks"],
                                  float(cm["synth_gain_db"]), cents)
    audio_io.save_wav(review_dir / "check_mix.wav", main, SR)
    if split is not None:
        audio_io.save_wav(review_dir / "check_bass_vs_chart.wav", split, SR)
    info(f"Check mix: {review_dir / 'check_mix.wav'}")

    # ------------------------------------------------------------- report
    report.add(f"Tuning: {tuning_note}")
    report.add(f"Rocksmith tuning offsets: {offsets[:4]}")
    report.add(f"Lead-in: first sound in the original audio at {first_sound:.2f} s; first beat (chart start) at "
               f"{chart.beats[0].time:.2f} s; first note at {first_note:.2f} s"
               + (f"; {pad:.2f} s of silence was added in front" if pad else ""))
    report.add(f"Notes: {len(chart.notes)}; measures: {sum(1 for b in chart.beats if b.measure >= 0)}; "
               f"difficulty levels: 1 (no dynamic difficulty)")
    report.add("Sections: " + ", ".join(f"{s.name} {_fmt(s.time)}" for s in chart.sections))
    if chart.flagged:
        report.add("")
        report.add("Sections where the tab alignment looks off (times in the check mix / game):")
        for a, b, r in chart.flagged:
            report.add(f"  {_fmt(a)} - {_fmt(b)}  {r}")

    psarc_path = None
    if package and cfg["packaging"]["enabled"]:
        step("Packaging (.psarc)")
        psarc_path = _package(rs2dlc, out_dir, cfg, report)
    else:
        report.add("")
        report.add("Packaging skipped. Open the .rs2dlc file in the project folder with RsPackager or DLC Builder.")

    warnings = collected_warnings()
    if warnings:
        report.add("")
        report.add("Warnings:")
        for w in warnings:
            report.add(f"  - {w}")
    report.add("")
    report.add("Files:")
    report.add(f"  CDLC:        {psarc_path or '(not built)'}")
    report.add(f"  Check mix:   {review_dir / 'check_mix.wav'}")
    report.add(f"  Stem/chart:  {review_dir / 'check_bass_vs_chart.wav'} (left = bass stem, right = chart synth)")
    report.add(f"  MIDI / GP5:  {review_dir / 'chart.mid'}, {review_dir / 'chart.gp5'}")
    if eof_path:
        report.add(f"  EOF project: {eof_path}")
    report.add(f"  Rocksmith XML (open in EOF via File > Import > Rocksmith): {xml_path}")
    report.add(f"  DLC Builder project: {rs2dlc}")
    (out_dir / "report.txt").write_text("\n".join(report.lines) + "\n", encoding="utf-8")
    step("Done")
    print("\n".join(report.lines[-7:]))
    return out_dir


def _write_eof(proj_dir: Path, audio_path: Path, xml_path: Path, cfg: dict) -> Path | None:
    """An EOF (Editor On Fire) project of the chart + the Ogg audio it needs, for hand edits."""
    cmd = find_packager(cfg)
    if not cmd:
        return None
    eof_dir = proj_dir / "eof"
    eof_dir.mkdir(exist_ok=True)
    ogg = eof_dir / "guitar.ogg"
    try:
        if not ogg.exists() or ogg.stat().st_mtime < audio_path.stat().st_mtime:
            audio_io.encode_ogg(audio_path, ogg)
        target = eof_dir / "notes.eof"
        r = subprocess.run(cmd + ["eof", str(xml_path), "--ogg", ogg.name, "--out", str(target)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip())
        info(f"EOF project: {target}")
        return target
    except Exception as e:  # noqa: BLE001 - optional extra
        warn(f"Could not create the EOF project ({e}); you can still import the XML in EOF.")
        return None


def _package(rs2dlc: Path, out_dir: Path, cfg: dict, report: Report) -> Path | None:
    cmd = find_packager(cfg)
    if not cmd:
        warn("RsPackager is not built, so no .psarc was made. Run setup.bat (see README), "
             "or open the .rs2dlc project in DLC Builder.")
        report.add("")
        report.add("Packaging: NOT DONE (RsPackager not found).")
        return None
    pc = cfg["packaging"]
    args = cmd + ["build", str(rs2dlc), "--out", str(out_dir)]
    if pc.get("wwise_console"):
        args += ["--wwise", pc["wwise_console"]]
    if pc.get("auto_volume"):
        args.append("--auto-volume")
    if pc.get("mac"):
        args.append("--mac")
    info("Running: " + " ".join(f'"{a}"' if " " in a else a for a in args))
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    built, issues, verify = [], [], []
    for line in (proc.stdout or "").splitlines():
        print("    " + line)
        if line.startswith("BUILT "):
            built.append(Path(line[6:].strip()))
        elif line.startswith("ISSUE "):
            issues.append(line[6:])
        elif line.startswith("VERIFY "):
            verify.append(line[7:])
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    report.add("")
    if proc.returncode != 0 or not built:
        err = (proc.stderr or "").strip().splitlines()
        warn("Packaging failed: " + (err[-1] if err else f"exit code {proc.returncode}"))
        report.add("Packaging: FAILED - " + (" ".join(err[-3:]) if err else ""))
        if "Wwise" in (proc.stderr or ""):
            report.add("  -> Install Wwise 2023 (see README, 'Wwise') or set packaging.wwise_console in config.yaml.")
        return None
    report.add("Packaging: headless build with RsPackager (DD generation off) - OK")
    for v in verify:
        report.add(f"  verify: {v}")
    if issues:
        report.add("  DLC Builder arrangement checks:")
        for i in issues:
            report.add(f"    {i}")
    psarc = built[0]
    dest = pc.get("copy_to_folder")
    if dest:
        try:
            for b in built:
                shutil.copy2(b, Path(dest) / b.name)
            info(f"Copied to {dest}")
            report.add(f"  copied to {dest}")
        except OSError as e:
            warn(f"Could not copy the .psarc to {dest}: {e}")
    return psarc
