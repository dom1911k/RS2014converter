"""Smoke test of the CDLC Maker window (needs a display; on Linux: xvfb-run python tests/smoke_gui.py TMPDIR shot.png).

A kit folder from the Bass Trainer extension is listed and built with a stand-in for make_cdlc."""
import importlib.util, sys, time, subprocess, os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
tmp = Path(sys.argv[1]); shot = sys.argv[2]
from rs2014conv import config, maker
config.USER_SETTINGS = tmp / 'settings.yaml'
config.save_user_setting('bass_trainer', 'songs_folder', str(tmp / 'songs'))
config.save_user_setting('packaging', 'copy_to_folder', str(tmp / 'dlc'))
kit = tmp / 'songs' / "Band - Love That's Gone"; kit.mkdir(parents=True)
(kit / 'song.yaml').write_text('title: "Love That\'s Gone"\nartist: "Band"\ntab: "x.btchart.json"\naudio: "song.wav"\nchart_offset: 2.37\n')
(kit / 'x.btchart.json').write_text('{"format": "bass-chart"}'); (kit / 'song.wav').write_bytes(b'RIFF')
fake = tmp / 'fake_make.py'
fake.write_text("import sys, pathlib, time\nsong = pathlib.Path(sys.argv[1]); out = song / 'output' / 'Band - Song'; (out / 'review').mkdir(parents=True, exist_ok=True)\n"
                "for s in ['Bass stem', 'Beat map', 'Reading tab', 'Aligning tab to audio', 'Packaging']:\n    print('== ' + s, flush=True); time.sleep(0.2)\n"
                "(out / 'report.txt').write_text('x\\nPLEASE CHECK:\\n  * Measures 12-13: tab and recording disagree, listen at 0:41.\\n'\n"
                "  '  * Wwise couldn\\'t convert the audio. Start Wwise once, then use Build the .psarc again.\\n\\nPackaging: FAILED - x\\n')\n"
                "(out / 'project').mkdir(exist_ok=True); (out / 'project' / 'Band_Song.rs2dlc').write_text('{}')\n"
                "import shutil, os; c = os.environ.get('SMOKE_COVER'); c and shutil.copy(c, out / 'project' / 'cover.png')\n"
                "(out / 'review' / 'check_mix.wav').write_bytes(b'w')\n")
# 'Build the .psarc again': a stand-in that makes the .psarc and updates the report.
refake = tmp / 'fake_repack.py'
refake.write_text("import sys, pathlib\nout = pathlib.Path(sys.argv[1]); print('== Packaging', flush=True)\n"
                  "(out / 'Band_Song_v1_p.psarc').write_bytes(b'p')\n"
                  "(out / 'report.txt').write_text('x\\nPLEASE CHECK:\\n  * Measures 12-13: tab and recording disagree, listen at 0:41.\\n\\nCover: iTunes: Lyn - Persona 5 (Original Soundtrack)\\nPackaging: headless build - OK\\n')\n")
maker.repackage_command = lambda out: [sys.executable, str(refake), str(out)]
maker.build_command = lambda folder: [sys.executable, str(fake), str(folder)]
from importlib.machinery import SourceFileLoader; m = SourceFileLoader('cdlc_maker', str(ROOT / 'app' / 'cdlc_maker.pyw')).load_module()
import tkinter as tk
root = tk.Tk(); app = m.App(root)
def pump(sec):
    end = time.time() + sec
    while time.time() < end: root.update(); time.sleep(0.02)
pump(1)
out = {'songs': list(app.listbox.get(0, 'end')), 'title': app.fields['title'].get(), 'audio': app.fields['audio'].get(), 'step': app.step_lbl.cget('text')}
app.make(); pump(0.4); out['during'] = app.step_lbl.cget('text')
t0 = time.time()
while app.proc and time.time() - t0 < 20: pump(0.2)
pump(0.5)
out['failedStep'] = app.step_lbl.cget('text')
out['repackVisible'] = app.repack_btn.winfo_ismapped()
app.repackage(); pump(0.3)
t0 = time.time()
while app.proc and time.time() - t0 < 20: pump(0.2)
pump(0.5)
out.update({'after': app.step_lbl.cget('text'), 'checks': app.checks_lbl.cget('text'), 'songsAfter': list(app.listbox.get(0, 'end')),
            'buttons': [b.cget('text') for b in (app.out_btn, app.mix_btn, app.report_btn) if b.winfo_ismapped()],
            'dlc': app.dlc_lbl.cget('text'), 'wwise': app.wwise_lbl.cget('text')})
subprocess.run(['import', '-window', 'root', shot])
# A dropped audio file: title and artist from the file name.
af = tmp / 'Muse - Hysteria.mp3'; af.write_bytes(b'x'); app.load(af); pump(0.3)
out['dropped'] = (app.fields['artist'].get(), app.fields['title'].get())
print(out)
root.destroy()
