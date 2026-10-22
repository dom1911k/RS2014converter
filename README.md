# make_cdlc — song audio (+ bass tab) → Rocksmith 2014 bass CDLC

Give it a song's audio file and, if you have one, a bass tab. You get back a finished
Rocksmith 2014 Remastered bass CDLC (`.psarc`) plus files to check and fix the chart.
Everything runs locally on your PC. After the one-time setup no internet is needed.

```
make_cdlc.bat "Song.flac" --title "Song" --artist "Band" --tab "Song.gp5"
```

* **With a tab** (best): the notes, rhythms and fingerings come from the tab. The tab is lined
  up with the recording beat by beat. This handles count-ins, repeats, a tempo that drifts,
  and a recording tuned differently from the tab. Places where tab and recording disagree are listed.
* **Without a tab**: the bass is separated from the mix and transcribed, then given
  sensible fingerings.
* **No Dynamic Difficulty**: the chart always has exactly one difficulty level, the full chart.
  Rocksmith shows every note from the first play.
* The `.psarc` is built **without opening DLC Builder**, using the same libraries DLC Builder uses
  (see [How the .psarc is built](#how-the-psarc-is-built-and-why-not-dlc-builder)).

---

## 1. One-time setup

You need: Windows 10/11, ~15 GB free disk space, internet during setup.

### 1a. Install Wwise 2023 (for the audio conversion)

Rocksmith needs its audio in Audiokinetic's `.wem` format, and only Wwise can make it. Wwise is free.

1. Go to <https://www.audiokinetic.com/download/>, create a free account and install the
   **Audiokinetic Launcher**.
2. In the Launcher, open the **Wwise** tab → **Install a new version** → choose the newest
   **2023.1.x** version. Newer versions (2024, 2025) do **not** work with the Rocksmith tools.
3. In the install options, tick only **Authoring** under *Packages*. Leave all *Deployment
   Platforms* and *Plug-ins* unticked.
4. When it's installed, **start Wwise once** (Launcher → Wwise 2023 → Launch) and close it again.
   The conversion may not work until you have done this once.

### 1b. Get this tool

Either download it with git, if you have git:

```
git clone --recursive https://github.com/dom1911k/RS2014converter.git
```

or on GitHub click **Code → Download ZIP** and unzip it somewhere, e.g. `C:\make_cdlc`.
Avoid folders that need admin rights, such as `C:\Program Files`.

### 1c. Run setup

Double-click **`setup.bat`**. It installs only what is missing:

| What | Why |
|---|---|
| Python 3.12 | runs the tool |
| a private Python environment (`.venv` folder) with PyTorch for your GPU | the AI models (Demucs, Beat This!, CREPE) |
| the AI models (downloaded once) | so it works offline afterwards |
| Rocksmith2014.NET source (DLC Builder's library) | to build the `.psarc` |
| .NET 10 SDK, then it builds `packager\publish\RsPackager.exe` | the headless packager |

It takes 10–30 minutes the first time and needs a few GB of downloads. You can run `setup.bat`
again at any time; it only repairs or updates what's needed. At the end it tells you if Wwise was found.

### 1d. (Optional) Install CDLC automatically

Open `config.yaml` in Notepad and set your Rocksmith `dlc` folder:

```yaml
packaging:
  copy_to_folder: 'C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014\dlc'
```

Then every finished `.psarc` is copied there, so you only need to start Rocksmith.
Use single quotes around the path.

---

## 2. Making a CDLC

Open a terminal in the tool's folder: in Explorer, click the address bar, type `cmd` and press Enter.

### Way 1: one command

```
make_cdlc.bat "C:\Music\Song.flac" --title "Song Title" --artist "Band"
make_cdlc.bat "C:\Music\Song.flac" --title "Song Title" --artist "Band" --tab "C:\Tabs\Song.gp5"
make_cdlc.bat "C:\Music\Song.mp3"  --title "Song" --artist "Band" --tuning "Drop D" --stem "C:\Music\bass.wav"
```

### Way 2: a song folder (easier to redo later)

Make a folder per song containing the audio file, optionally the tab, a bass stem (an audio file with
"bass" or "stem" in its name) and a cover image, and
a `song.yaml` (copy `examples\MySong\song.yaml` and edit it in Notepad):

```yaml
title: Song Title
artist: Band
album: Album Name
year: 1999
tuning: Drop D        # optional
```

Then run:

```
make_cdlc.bat "C:\Songs\Song Title"
```

The results go to an `output` folder inside the song folder. To redo the song after changing
something, run the same command again. Separation and beat tracking are cached, so reruns are fast.

### All options

| Option | What it does |
|---|---|
| `--title`, `--artist` | required (or in `song.yaml`) |
| `--album`, `--year` | optional, shown in the game |
| `--tab FILE` | Guitar Pro `.gp3 .gp4 .gp5 .gpx .gp` (best), or a plain-text tab `.txt` (best effort) |
| `--track N` | which track of the tab is the bass (1 = first). Normally detected automatically |
| `--stem FILE` | an isolated bass track: skips the separation |
| `--tuning "..."` | e.g. `"E Standard"`, `"Drop D"`, `"Eb Standard"`, `"D Standard"`, `"Drop C"`, `"B E A D G"`. If you leave it out, the tuning is taken from the tab, or guessed from the lowest notes (the guess is shown in the report) |
| `--strings 5` | you play a 5-string (helps the tuning guess) |
| `--cover FILE` | album art (any image). If missing, a simple cover with the title is made |
| `--grid 1/16` | rhythm grid when there's no tab: `1/8`, `1/16`, `1/8T`, `1/16T`, `1/16+T` (straight or triplets, chosen per beat) |
| `--picked` | mark the arrangement as played with a pick |
| `--out FOLDER` | put the results somewhere else |
| `--no-package` | make everything except the `.psarc` |
| `--config FILE` | use a different settings file instead of `config.yaml` |

All the finer settings (sensitivity, grid, sustain lengths, check-mix volume…) are in
`config.yaml`, each with a comment. Any of them can also be set per song in `song.yaml`.

---

## 3. What you get

```
output\Band - Song Title\
  Band_Song-Title_v1_p.psarc        <- the CDLC. Copy it into Rocksmith2014\dlc
  report.txt                        <- READ THIS: tuning, tempo, warnings, places to check
  review\
    check_mix.wav                   <- the song + a synth bass playing the chart + clicks
    check_bass_vs_chart.wav         <- left ear: the real bass alone, right ear: the chart
    chart.gp5                       <- the chart as Guitar Pro tab (tempo follows the song)
    chart.mid                       <- the chart as MIDI (timed exactly like the audio)
  project\
    arr_bass_RS2.xml                <- the Rocksmith arrangement (open/fix it in EOF)
    Band_Song Title.rs2dlc          <- DLC Builder project (for rebuilding)
    audio.wav, cover.png, ids.json  <- inputs for the package (keep ids.json!)
```

### Checking the chart (do this before playing)

1. **Read `report.txt`.** It says which tuning was used (and whether it was guessed), the tempo,
   where the chart starts, and, for tabs, **which measures look misaligned**, with times.
2. **Listen to `review\check_mix.wav`.** You hear the song with a simple synth playing exactly what
   Rocksmith will show, plus a click on every beat (higher click = first beat of a bar).
   If the synth and the real bass agree, the chart is right. Listen especially at the times
   listed in the report.
3. **Hard to hear?** `check_bass_vs_chart.wav` puts the separated real bass in your left ear and the
   chart in your right ear.
4. **Want to see the notes?** Open `review\chart.gp5` in Guitar Pro (or the free TuxGuitar /
   MuseScore), or `chart.mid` in any MIDI program.

### Fixing something

* **Wrong tuning guess** → run again with `--tuning "..."`.
* **Wrong tab track** → `--track N` (the console lists the tracks when it starts).
* **Too many / too few notes without a tab** → in `config.yaml` change `transcription:
  min_confidence` (lower = more notes) or `onset_sensitivity` (higher = more repeated notes),
  or try `--grid 1/8` for simple lines.
* **Fix notes by hand** in **EOF** (Editor On Fire): *File → Import → Rocksmith* and pick
  `project\arr_bass_RS2.xml`. Fix it, then export with *File → Export → Rocksmith 2014* and save
  over `project\arr_bass_RS2.xml`. Then rebuild only the package:
  ```
  build_psarc.bat "C:\Songs\Song\output\Band - Song\project\Band_Song.rs2dlc"
  ```
  (Don't run `make_cdlc` again after hand fixes: it would overwrite the XML.)
  In EOF, don't add difficulty levels if you want to keep the no-DD chart.

### Updating a CDLC you already have in Rocksmith

The IDs in `project\ids.json` are reused, so a rebuilt `.psarc` replaces the old one in the game.
Your scores are kept. Just overwrite the file in the `dlc` folder. To release a "fixed" version,
put `version: "2"` in `song.yaml`.

---

## 4. How it works (short version)

1. **Bass stem**: Demucs `htdemucs_ft` (the best Demucs model for bass) separates the bass. Only its
   bass sub-model runs, so it's 4× faster than the full model. Cached in the `cache` folder.
2. **Beat map**: *Beat This!* (CPJKU, 2024, state of the art) finds beats and downbeats in the full mix. Every beat keeps its
   own time, so tempo drift, ritardandos etc. are followed exactly. A constant tracker latency is
   corrected against the mix's onsets. The lead-in is detected (first sound, first beat); if the bass
   starts too early for a count-in, silence is added in front of the audio, and every time is shifted to match.
3. **Notes**
   * *Tab*: Guitar Pro 3–8 is read (GP6/7/8 by a built-in reader). Repeats, alternate endings and
     D.C./D.S./Coda/Fine are expanded. The tab and the bass stem are compared beat by beat (bass pitch
     classes + attacks), and dynamic time warping matches tab beats to the recording's beats. It also
     tries the beat grid at double/half tempo, and transpositions of ±5 semitones (to detect e.g. a recording
     tuned half a step down). Count-in bars before the music are dropped. Bars the recording has but the
     tab doesn't are skipped (and reported). Techniques from the tab are kept: hammer-on/pull-off,
     slides, palm mute, dead notes, harmonics, vibrato, bends, slap/pop, tapping, accents.
   * *No tab*: CREPE pitch tracking (GPU) and two onset detectors (spectral + energy re-attacks, so
     repeated notes of the same pitch are found) split the stem into notes. Octave errors are fixed
     using the bass register and the spectrum (a real low note has energy at its fundamental and fifth).
     Fragments are merged, very short/unsure notes dropped, then everything is snapped to the beat
     map's grid. With synthetic test songs CREPE found 100% of the notes (pYIN, the fallback, ~88%).
4. **Fingering**: a dynamic-programming optimiser picks string and fret for every note. It minimises
   hand position shifts (more strongly for fast passages), prefers low positions and open strings,
   and respects the tuning and string count. With a tab, the tab's fingering is kept.
5. **Rocksmith files**: arrangement XML with beats synced to the audio, notes, sustains,
   techniques, tuning, hand anchors, phrases and sections. Sections come from the tab's markers, or
   are detected from the song's structure and named verse/chorus/bridge… (best effort).
   There is **one difficulty level**: every phrase has `maxDifficulty="0"`.
6. **Package**: `RsPackager` (in `packager\`) builds the `.psarc` with Rocksmith2014.NET. It does
   SNG conversion, encryption, manifests, preview clip, volume, album art and show lights, and
   converts the audio with Wwise. DD generation is switched off. Afterwards it reopens the
   package and checks that each arrangement has exactly one level. DLC Builder's arrangement checks run
   too, and any issues are listed in `report.txt`.

### How the .psarc is built, and why not DLC Builder

**The `.psarc` is built headlessly; you never need to open DLC Builder.**

DLC Builder only lets you turn off DD generation for **test builds**. Its **release builds
always generate DD** when a chart has a single level (see `ReleasePackageBuilder`/`BuildConfig.fs` in
Rocksmith2014.NET, and the "Test Build vs Release Build" section of its README). So `RsPackager` calls
the same library functions DLC Builder calls, with `GenerateDD = false`.

The `.rs2dlc` project file is still written, in case you want to open the song in DLC Builder (e.g. to
change the tone). If you build from DLC Builder, use **Build → Test** with **Generate DD levels**
unticked in its configuration. A **Release** build from DLC Builder adds DD.

### Tunings, 5-string and low tunings

* Rocksmith basses have 4 strings; tunings are stored as offsets from E standard.
* **5-string** (B E A D G): the chart uses the lowest four strings (B E A D, shown in the game as a
  bass tuned down 5 semitones). Notes on the G string are moved to the D string.
  The report says so.
* Very low tunings (any string more than 4 semitones down, e.g. B standard) get DLC Builder's
  **low tuning fix** automatically (tuning pitch 220 Hz), otherwise the game can't detect the notes.
* Low B (31 Hz) is below CREPE's range. In the no-tab path it's found an octave up, and the octave check
  should move it back down (not tested on real recordings). Give `--tuning` for 5-string songs to be safe.

---

## 5. Troubleshooting

| Message / problem | What to do |
|---|---|
| `Setup has not been run yet` | double-click `setup.bat` |
| `Could not find Wwise ... installation` | install Wwise **2023** (section 1a) and start it once. If it's installed somewhere unusual, set `packaging: wwise_console:` in `config.yaml` to the full path of `WwiseConsole.exe` (in `Authoring\x64\Release\bin`) |
| `Beat This! failed ... using the librosa tracker` | the beat model wasn't downloaded. Run `setup.bat` again while online. The simple tracker works but is less accurate on hard songs |
| `RsPackager is not built` | run `setup.bat` again and look for errors in step 5 |
| `The chosen tab track has no notes` | use `--track N` |
| report says *tab and audio don't match well* | is it the same version of the song (live/studio, a radio edit)? Listen to the check mix there |
| GPU not used (slow) | `setup.bat` installs the CUDA version only if `nvidia-smi` works. Update the NVIDIA driver and run `setup.bat` again |
| song doesn't show in Rocksmith | the CDLC enabler must be installed. The file must end in `_p.psarc` and be in `Rocksmith2014\dlc` |

To start completely fresh, delete the `.venv` and `cache` folders and run `setup.bat`.

---

## 6. For tinkering / testing

* `tests\make_synthetic.py OUTDIR` makes a test song with a known bass line, drifting tempo, a GP5
  tab (with count-in and repeats) and a `truth.json`. `tests\evaluate.py OUTPUT_SONG_FOLDER truth.json`
  scores a result. Options: `--tuning-shift -1` (recording half a step down), `--form AABBBCDAA`
  (recording has an extra bar), `--fundamental 0.12` (weak fundamental → octave-error test).
* `.venv\Scripts\python -m pytest tests` runs the unit tests.
* `packager\publish\RsPackager.exe check project.rs2dlc` runs DLC Builder's checks on an
  arrangement. `RsPackager.exe verify file.psarc` shows the levels and notes in a package.

Code layout: `make_cdlc.py` (command line) → `rs2014conv\pipeline.py` (all steps) using
`separation.py`, `beats.py`, `tabs.py` (tab readers), `tab_align.py`, `transcribe.py`, `fingering.py`,
`sections.py`, `rsxml.py`, `project.py`, `exports.py`, `checkmix.py`. The packager is
`packager\Program.fs`.

## Credits and licences

* [Rocksmith2014.NET / DLC Builder](https://github.com/iminashi/Rocksmith2014.NET) by iminashi (MIT): packaging, SNG, checks
* [Demucs](https://github.com/facebookresearch/demucs) (MIT): source separation
* [Beat This!](https://github.com/CPJKU/beat_this) (MIT): beat tracking
* [torchcrepe](https://github.com/maxrmorrison/torchcrepe) (MIT): pitch tracking
* [librosa](https://librosa.org) (ISC), [PyGuitarPro](https://github.com/Perlence/PyGuitarPro) (LGPL), [mido](https://github.com/mido/mido) (MIT)
* Wwise is © Audiokinetic and is installed separately under its own licence.

Use this for songs you own, for personal practice.
