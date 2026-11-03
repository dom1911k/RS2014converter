"""RS2014 CDLC Maker: a small window for make_cdlc.

Start it from the desktop / Start menu shortcut (made by setup), or drop an audio file, a tab
or a song folder on the shortcut. Songs from the Bass Trainer extension appear in the list.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rs2014conv import config, maker  # noqa: E402
from rs2014conv.desktop import WWISE_DOWNLOAD, find_wwise  # noqa: E402

BG, PANEL, TEXT, MUTED, ACCENT, RED, GREEN = "#15171c", "#1d2027", "#e8e8ea", "#9aa0aa", "#f2c418", "#e8202a", "#5fd38d"
AUDIO_TYPES = [("Audio", " ".join(f"*{e}" for e in sorted(maker.AUDIO_EXT))), ("All files", "*.*")]
TAB_TYPES = [("Tabs", "*.gp3 *.gp4 *.gp5 *.gpx *.gp *.btchart.json *.txt"), ("All files", "*.*")]


def open_path(p: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(p))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(p)])


class App:
    def __init__(self, root: tk.Tk, start: Path | None = None):
        self.root = root
        self.song: maker.Song | None = None
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self.songs: list = []
        root.title("RS2014 CDLC Maker")
        root.geometry("980x680")
        root.minsize(820, 560)
        root.configure(bg=BG)
        icon = ROOT / "app" / "icon.ico"
        if icon.exists() and sys.platform == "win32":
            root.iconbitmap(str(icon))
        self._style()
        self._layout()
        self.refresh_status()
        self.refresh_songs()
        if start:
            self.load(start)
        elif self.songs:
            self.select(0)
        threading.Thread(target=self._check_update, daemon=True).start()
        root.after(100, self._pump)

    # ------------------------------------------------------------------ layout
    def _style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL, bordercolor="#3a3f49", font=("Segoe UI", 10))
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=TEXT)
        s.configure("Panel.TLabel", background=PANEL)
        s.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        s.configure("Head.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 15, "bold"))
        s.configure("Sub.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        s.configure("Ok.TLabel", background=PANEL, foreground=GREEN)
        s.configure("Warn.TLabel", background=PANEL, foreground="#ffb347")
        s.configure("TButton", background="#2a2e36", foreground=TEXT, padding=(10, 5), borderwidth=1)
        s.map("TButton", background=[("active", "#343944"), ("disabled", "#20232a")], foreground=[("disabled", "#5a606b")])
        s.configure("Big.TButton", background=RED, foreground="#ffffff", font=("Segoe UI", 11, "bold"), padding=(18, 8))
        s.map("Big.TButton", background=[("active", "#ff3a44"), ("disabled", "#5a2a2e")])
        s.configure("TEntry", fieldbackground="#0e1014", foreground=TEXT, insertcolor=TEXT)
        s.configure("TCombobox", fieldbackground="#0e1014", foreground=TEXT)
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor="#0e1014")

    def _layout(self):
        top = ttk.Frame(self.root, padding=(14, 10))
        top.pack(fill="x")
        ttk.Label(top, text="RS2014 CDLC Maker", style="Head.TLabel").pack(side="left")
        self.update_btn = ttk.Button(top, text="Check for updates", command=self.run_update)
        self.update_btn.pack(side="right")
        self.version_lbl = ttk.Label(top, text="", foreground=MUTED)
        self.version_lbl.pack(side="right", padx=8)

        # The status bar first, so it always keeps its place at the bottom.
        bottom = ttk.Frame(self.root, style="Panel.TFrame", padding=(14, 8))
        bottom.pack(fill="x", side="bottom")
        self.dlc_lbl = ttk.Label(bottom, text="", style="Panel.TLabel")
        self.dlc_lbl.pack(side="left")
        ttk.Button(bottom, text="Change…", command=self.change_dlc).pack(side="left", padx=6)
        self.wwise_btn = ttk.Button(bottom, text="How to install Wwise", command=self.wwise_help)
        self.wwise_lbl = ttk.Label(bottom, text="", style="Panel.TLabel")
        self.wwise_lbl.pack(side="right")

        body = ttk.Frame(self.root, padding=(14, 0, 14, 8))
        body.pack(fill="both", expand=True)
        # Songs list
        left = ttk.Frame(body, style="Panel.TFrame", padding=10)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="YOUR SONGS", style="Sub.TLabel").pack(anchor="w")
        self.listbox = tk.Listbox(left, width=34, bg="#0e1014", fg=TEXT, selectbackground="#3a3110", selectforeground=ACCENT,
                                  highlightthickness=0, borderwidth=0, activestyle="none", font=("Segoe UI", 10))
        self.listbox.pack(fill="y", expand=True, pady=6)
        self.listbox.bind("<<ListboxSelect>>", lambda e: self.listbox.curselection() and self.select(self.listbox.curselection()[0]))
        ttk.Button(left, text="+ New song (choose audio)…", command=self.new_song).pack(fill="x")
        ttk.Button(left, text="Open a song folder…", command=self.open_folder).pack(fill="x", pady=(6, 0))
        ttk.Label(left, text="Songs from the Bass Trainer extension\nappear here too.", style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        right = ttk.Frame(body, style="Panel.TFrame", padding=14)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.fields = {}
        form = ttk.Frame(right, style="Panel.TFrame")
        form.pack(fill="x")
        for r, (key, label) in enumerate([("title", "Title"), ("artist", "Artist"), ("album", "Album (optional)"), ("year", "Year (optional)")]):
            ttk.Label(form, text=label, style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=3)
            e = ttk.Entry(form, width=48)
            e.grid(row=r, column=1, columnspan=2, sticky="we", pady=3, padx=(8, 0))
            self.fields[key] = e
        for r, (key, label, cmd) in enumerate([("audio", "Audio", self.pick_audio), ("tab", "Bass tab (optional)", self.pick_tab)], start=4):
            ttk.Label(form, text=label, style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=3)
            e = ttk.Entry(form, width=48)
            e.grid(row=r, column=1, sticky="we", pady=3, padx=(8, 6))
            ttk.Button(form, text="Choose…", command=cmd).grid(row=r, column=2, sticky="e")
            self.fields[key] = e
        ttk.Label(form, text="Tuning", style="Panel.TLabel").grid(row=6, column=0, sticky="w", pady=3)
        self.tuning = ttk.Combobox(form, width=20, values=["(automatic)", "E Standard", "Drop D", "Eb Standard", "D Standard",
                                                          "Drop C", "C Standard", "B E A D G"])
        self.tuning.grid(row=6, column=1, sticky="w", pady=3, padx=(8, 0))
        self.tuning.set("(automatic)")
        form.columnconfigure(1, weight=1)
        ttk.Label(right, text="Without a tab the bass is separated from the song and transcribed. With a tab "
                  "(Guitar Pro, or a Bass Trainer chart) the notes come from the tab.", style="Muted.TLabel",
                  wraplength=560).pack(anchor="w", pady=(4, 10))

        actions = ttk.Frame(right, style="Panel.TFrame")
        actions.pack(fill="x")
        self.make_btn = ttk.Button(actions, text="⚡ Make CDLC", style="Big.TButton", command=self.make)
        self.make_btn.pack(side="left")
        self.cancel_btn = ttk.Button(actions, text="Stop", command=self.cancel)
        results = ttk.Frame(right, style="Panel.TFrame")
        results.pack(fill="x", pady=(8, 0))
        self.out_btn = ttk.Button(results, text="Open output folder", command=lambda: self.result and open_path(self.result.out_dir))
        self.mix_btn = ttk.Button(results, text="▶ Listen to the check mix", command=lambda: self.result and self.result.check_mix and open_path(self.result.check_mix))
        self.repack_btn = ttk.Button(results, text="⟳ Build the .psarc again", command=self.repackage)
        self.report_btn = ttk.Button(results, text="Report", command=lambda: self.result and open_path(self.result.out_dir / "report.txt"))
        self.progress = ttk.Progressbar(right, mode="indeterminate")
        self.step_lbl = ttk.Label(right, text="", style="Panel.TLabel")
        self.step_lbl.pack(anchor="w", pady=(10, 2))
        self.checks_lbl = ttk.Label(right, text="", style="Warn.TLabel", wraplength=600, justify="left")
        self.checks_lbl.pack(anchor="w")
        self.log = tk.Text(right, height=8, bg="#0e1014", fg="#aeb5c4", insertbackground=TEXT, borderwidth=0,
                           font=("Consolas", 9), wrap="word")
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        self.result: maker.Result | None = None

    # ------------------------------------------------------------------ status
    def refresh_status(self):
        cfg = config.load(None)
        dest = (cfg.get("packaging") or {}).get("copy_to_folder") or ""
        self.dlc_lbl.configure(text=f"Finished CDLC go to: {dest}" if dest else "Finished CDLC stay in each song's output folder (no Rocksmith dlc folder set)")
        if find_wwise(cfg):
            self.wwise_lbl.configure(text="Wwise ✓", style="Ok.TLabel")
            self.wwise_btn.pack_forget()
        else:
            self.wwise_lbl.configure(text="Wwise missing: the .psarc can't be made yet", style="Warn.TLabel")
            self.wwise_btn.pack(side="right", padx=6)
        v = maker.installed_version()
        self.version_lbl.configure(text=f"version {v[:7]}" if v else "")

    def _check_update(self):
        newest = maker.newest_version()
        mine = maker.installed_version()
        if newest and mine and newest[0] != mine:
            self.root.after(0, lambda: (self.update_btn.configure(text="⬆ Update available: install"),
                                        self.version_lbl.configure(text=f"new: {newest[1][:60]}")))

    def run_update(self):
        script = ROOT / "install.ps1"
        if sys.platform != "win32" or not script.exists():
            messagebox.showinfo("Update", "Run install.ps1 -Update (Windows) to update.")
            return
        if self.proc:
            messagebox.showinfo("Update", "Wait until the CDLC is finished.")
            return
        subprocess.Popen(["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Update"],
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.root.destroy()   # the updater starts the app again when it's done

    def change_dlc(self):
        d = filedialog.askdirectory(title="Rocksmith 2014's dlc folder (Steam\\steamapps\\common\\Rocksmith2014\\dlc)")
        if d:
            config.save_user_setting("packaging", "copy_to_folder", d)
            self.refresh_status()

    def wwise_help(self):
        messagebox.showinfo("Install Wwise (once)",
                            "Rocksmith needs its audio in Wwise's format, and only Wwise can make it. It is free, "
                            "but needs an Audiokinetic account:\n\n"
                            "1. On the page that opens: create an account, download the Audiokinetic Launcher.\n"
                            "2. Launcher → Wwise tab → Install a new version → the newest 2023.1.x (not 2024/2025).\n"
                            "3. Tick only 'Authoring' under Packages, then Install.\n"
                            "4. Start Wwise once (Launcher → Wwise 2023 → Launch) and close it.\n\n"
                            "Then come back here: Wwise is found automatically.")
        webbrowser.open(WWISE_DOWNLOAD)
        self.root.after(5000, self.refresh_status)

    # ------------------------------------------------------------------ songs
    def refresh_songs(self, keep: Path | None = None):
        self.songs = maker.list_songs()
        self.listbox.delete(0, "end")
        for folder, s, res in self.songs:
            mark = "✓ " if res and res.psarc else "• "
            self.listbox.insert("end", f"{mark}{s.artist} – {s.title}" if s.artist else f"{mark}{s.title}")
        if keep:
            for i, (folder, _, _) in enumerate(self.songs):
                if folder == keep:
                    self.listbox.selection_clear(0, "end")
                    self.listbox.selection_set(i)

    def select(self, i: int):
        if self.proc or i >= len(self.songs):
            return
        folder, song, res = self.songs[i]
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(i)
        self.show(song, res)

    def show(self, song: maker.Song, res: maker.Result | None = None):
        self.song = song
        for k in ("title", "artist", "album"):
            self.fields[k].delete(0, "end")
            self.fields[k].insert(0, getattr(song, k))
        self.fields["year"].delete(0, "end")
        if song.year:
            self.fields["year"].insert(0, str(song.year))
        for k in ("audio", "tab"):
            self.fields[k].delete(0, "end")
            p = song.path(getattr(song, k))
            self.fields[k].insert(0, str(p) if p else "")
        self.tuning.set(song.tuning or "(automatic)")
        self.show_result(res)
        self.log.delete("1.0", "end")
        if song.extra.get("chart_offset") is not None:
            self.step_lbl.configure(text=f"Synced in the Bass Trainer player (the tab starts {float(song.extra['chart_offset']):+.3f} s into the audio).")

    def show_result(self, res: maker.Result | None):
        self.result = res
        for b in (self.out_btn, self.mix_btn, self.report_btn, self.repack_btn):
            b.pack_forget()
        if res:
            if not res.psarc and res.rs2dlc:
                self.repack_btn.pack(side="left", padx=(0, 8))
            self.out_btn.pack(side="left")
            if res.check_mix:
                self.mix_btn.pack(side="left", padx=(8, 0))
            self.report_btn.pack(side="left", padx=(8, 0))
            done = "✓ CDLC made" if res.psarc else ("Made everything except the .psarc. Fix what's listed below, then "
                                                    "press ⟳ Build the .psarc again (takes seconds).")
            self.step_lbl.configure(text=done, style="Ok.TLabel" if res.psarc else "Warn.TLabel")
            self.checks_lbl.configure(text=("Please check:\n• " + "\n• ".join(res.checks)) if res.checks else "Nothing special to check. Still listen to the check mix once.")
        else:
            self.step_lbl.configure(text="", style="Panel.TLabel")
            self.checks_lbl.configure(text="")

    def load(self, path: Path):
        path = Path(path)
        try:
            song = maker.load_song(path)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Can't open", str(e))
            return
        res = maker.latest_result(path) if path.is_dir() else None
        self.show(song, res)

    def new_song(self):
        f = filedialog.askopenfilename(title="The song's audio file", filetypes=AUDIO_TYPES)
        if f:
            self.load(Path(f))
            self.fields["title"].focus_set()

    def open_folder(self):
        d = filedialog.askdirectory(title="A song folder (with song.yaml, or the audio and tab)")
        if d:
            self.load(Path(d))

    def pick_audio(self):
        f = filedialog.askopenfilename(title="The song's audio file", filetypes=AUDIO_TYPES)
        if f:
            self.fields["audio"].delete(0, "end")
            self.fields["audio"].insert(0, f)
            if not self.fields["title"].get():
                a, t = maker.guess_from_name(Path(f))
                self.fields["title"].insert(0, t)
                if a and not self.fields["artist"].get():
                    self.fields["artist"].insert(0, a)

    def pick_tab(self):
        f = filedialog.askopenfilename(title="Bass tab (optional)", filetypes=TAB_TYPES)
        if f:
            self.fields["tab"].delete(0, "end")
            self.fields["tab"].insert(0, f)

    # ------------------------------------------------------------------ build
    def form_song(self) -> maker.Song:
        s = self.song or maker.Song()
        s.title, s.artist, s.album = (self.fields[k].get().strip() for k in ("title", "artist", "album"))
        y = self.fields["year"].get().strip()
        s.year = int(y) if y.isdigit() else 0
        s.audio, s.tab = self.fields["audio"].get().strip(), self.fields["tab"].get().strip()
        t = self.tuning.get().strip()
        s.tuning = "" if t.startswith("(") else t
        return s

    def make(self):
        if self.proc:
            return
        s = self.form_song()
        missing = s.missing()
        if missing:
            messagebox.showwarning("Almost", "Please give " + ", ".join(missing) + ".")
            return
        folder = maker.save_song(s)
        self.song = s
        self.refresh_songs(keep=folder)
        self._run(maker.build_command(folder), folder, "Starting… (the first song takes a few minutes; later ones are faster)")

    def repackage(self):
        if self.proc or not self.result or not self.song or not self.song.folder:
            return
        self.refresh_status()
        self._run(maker.repackage_command(self.result.out_dir), self.song.folder, "Building the .psarc…")

    def _run(self, cmd: list[str], folder: Path, first: str):
        self.show_result(None)
        self.log.delete("1.0", "end")
        self.step_lbl.configure(text=first, style="Panel.TLabel")
        self.make_btn.configure(state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        self.progress.pack(fill="x", before=self.step_lbl)
        self.progress.start(12)
        self.run_started = time.time()
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._read, args=(self.proc, folder), daemon=True).start()

    def _read(self, proc, folder):
        for raw in proc.stdout:
            self.lines.put(("line", raw.decode("utf-8", errors="replace").rstrip()))
        self.lines.put(("done", (proc.wait(), folder)))

    def _pump(self):
        try:
            while True:
                kind, val = self.lines.get_nowait()
                if kind == "line" and val:
                    self.log.insert("end", val + "\n")
                    self.log.see("end")
                    if val.startswith("== "):
                        self.step_lbl.configure(text=val[3:], style="Panel.TLabel")
                elif kind == "done":
                    self.finished(*val)
        except queue.Empty:
            pass
        self.root.after(100, self._pump)

    def finished(self, code: int, folder: Path):
        self.proc = None
        self.progress.stop()
        self.progress.pack_forget()
        self.cancel_btn.pack_forget()
        self.make_btn.configure(state="normal")
        res = maker.latest_result(folder)
        self.refresh_songs(keep=folder)
        # Only a report written by this run counts (a stopped run leaves the old one).
        if res and (res.out_dir / "report.txt").stat().st_mtime >= self.run_started - 1:
            self.show_result(res)
            if res.psarc:
                cfg = config.load(None)
                dest = (cfg.get("packaging") or {}).get("copy_to_folder")
                self.step_lbl.configure(text="✓ CDLC made" + (" and copied to Rocksmith: start the game!" if dest else ""), style="Ok.TLabel")
        else:
            self.step_lbl.configure(text="Stopped. The reason is at the end of the log.", style="Warn.TLabel")

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def main():
    start = Path(sys.argv[1]) if len(sys.argv) > 1 and Path(sys.argv[1]).exists() else None
    root = tk.Tk()
    # The app has no console: show errors in a message and keep them in app/last_error.txt.
    def report(*exc):
        import traceback

        text = "".join(traceback.format_exception(*exc))
        (ROOT / "app" / "last_error.txt").write_text(text, encoding="utf-8")
        messagebox.showerror("RS2014 CDLC Maker", f"Something went wrong:\n\n{exc[1]}\n\n(Details: app\\last_error.txt)")
    root.report_callback_exception = report
    try:
        App(root, start)
    except Exception:  # noqa: BLE001
        report(*sys.exc_info())
        return
    root.mainloop()


if __name__ == "__main__":
    main()
