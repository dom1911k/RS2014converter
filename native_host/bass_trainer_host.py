"""Bass Trainer -> RS2014converter link (Chrome "native messaging" host).

The Bass Trainer browser extension sends a song folder (song.yaml, the chart and your audio
file); this program saves it in the converter's songs folder and runs make_cdlc on it, sending
the progress back. Chrome starts it (registered by setup.bat) only for the Bass Trainer
extension and talks to it over stdin/stdout: each message is 4 bytes of length (little endian)
followed by UTF-8 JSON.

    -> hello                              <- hello {version, songsDir, dlcFolder, wwise}
    -> begin {folder, files, audio}       <- ready
    -> chunk {data: base64} (repeated)    <- chunk-ok
    -> end                                <- saved {dir}
    -> build                              <- step / log ... then done {ok, psarc, outDir, checks, copiedTo, error}
    -> open {folder, what}                <- opened
    -> cancel                             (stops a running build)

Nothing is printed to stdout except messages (Chrome would drop the connection).
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import subprocess
import sys
import threading
from pathlib import Path

VERSION = 1
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_out_lock = threading.Lock()


def _binary_stdio() -> None:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def read_message(stream=None) -> dict | None:
    stream = stream or sys.stdin.buffer
    head = stream.read(4)
    if len(head) < 4:
        return None
    (n,) = struct.unpack("<I", head)
    data = stream.read(n)
    if len(data) < n:
        return None
    return json.loads(data.decode("utf-8"))


def send(msg: dict, stream=None) -> None:
    stream = stream or sys.stdout.buffer
    data = json.dumps(msg).encode("utf-8")
    if len(data) > 1_000_000:  # Chrome's limit for messages from the host
        msg = {k: (v[:2000] if isinstance(v, str) else v) for k, v in msg.items()}
        data = json.dumps(msg).encode("utf-8")
    with _out_lock:
        stream.write(struct.pack("<I", len(data)) + data)
        stream.flush()


def safe_name(name: str) -> str:
    """A plain file / folder name: no folders, no drive letters, no '..'."""
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name or name in (".", ".."):
        raise ValueError("bad file name")
    return name[:120]


def load_cfg() -> dict:
    from rs2014conv import config as config_mod

    return config_mod.load(None)


def songs_dir(cfg: dict) -> Path:
    custom = ((cfg.get("bass_trainer") or {}).get("songs_folder") or "").strip()
    return Path(custom) if custom else ROOT / "songs"


def wwise_found() -> bool | None:
    if sys.platform != "win32":
        return None
    if os.environ.get("WWISEROOT"):
        return True
    for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if pf and (Path(pf) / "Audiokinetic").exists():
            return True
    return False


def report_checks(report: Path) -> list[str]:
    """The 'PLEASE CHECK' items of report.txt."""
    if not report.exists():
        return []
    out, inside = [], False
    for line in report.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("PLEASE CHECK"):
            inside = True
            continue
        if inside:
            if line.startswith("  * "):
                out.append(line[4:])
            elif not line.strip():
                break
    return out


class Host:
    def __init__(self, write=send, build_cmd=None):
        self.write = write
        self.cfg = load_cfg()
        self.dir: Path | None = None
        self.audio = None
        self.audio_path: Path | None = None
        self.audio_size = 0
        self.proc: subprocess.Popen | None = None
        # The command that builds a song folder (tests replace it).
        self.build_cmd = build_cmd or [sys.executable, str(ROOT / "make_cdlc.py")]

    def handle(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "hello":
            self.write({"type": "hello", "version": VERSION, "songsDir": str(songs_dir(self.cfg)),
                        "dlcFolder": (self.cfg.get("packaging") or {}).get("copy_to_folder") or "",
                        "wwise": wwise_found()})
        elif t == "begin":
            folder = songs_dir(self.cfg) / safe_name(msg["folder"])
            folder.mkdir(parents=True, exist_ok=True)
            for f in msg.get("files") or []:
                (folder / safe_name(f["name"])).write_text(f["text"], encoding="utf-8", newline="\n")
            self.dir = folder
            audio = msg.get("audio")
            if audio:
                self.audio_path = folder / safe_name(audio["name"])
                self.audio_size = int(audio.get("size") or 0)
                self.audio = open(self.audio_path, "wb")
            self.write({"type": "ready", "dir": str(folder)})
        elif t == "chunk":
            if self.audio is None:
                raise ValueError("chunk without begin")
            self.audio.write(base64.b64decode(msg["data"]))
            self.write({"type": "chunk-ok"})
        elif t == "end":
            if self.audio is not None:
                self.audio.close()
                self.audio = None
                got = self.audio_path.stat().st_size
                if self.audio_size and got != self.audio_size:
                    raise ValueError(f"audio incomplete ({got} of {self.audio_size} bytes)")
            self.write({"type": "saved", "dir": str(self.dir)})
        elif t == "build":
            # In the background, so a "cancel" can still be read meanwhile.
            threading.Thread(target=self._build_safe, daemon=True).start()
        elif t == "open":
            self.open_folder(msg)
        elif t == "cancel":
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()

    def _build_safe(self) -> None:
        try:
            self.build()
        except Exception as e:  # noqa: BLE001
            self.write({"type": "done", "ok": False, "error": str(e)})

    def build(self) -> None:
        if self.dir is None:
            raise ValueError("nothing to build")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(self.build_cmd + [str(self.dir)], cwd=str(ROOT), stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        last_error = ""
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            if line.startswith("== "):
                self.write({"type": "step", "text": line[3:]})
            if line.startswith("FAILED:") or "ERROR" in line:
                last_error = line
            self.write({"type": "log", "line": line})
        code = self.proc.wait()
        out_root = self.dir / "output"
        reports = sorted(out_root.glob("*/report.txt"), key=lambda p: p.stat().st_mtime) if out_root.exists() else []
        out_dir = reports[-1].parent if reports else None
        psarcs = sorted(out_dir.glob("*.psarc"), key=lambda p: p.stat().st_mtime) if out_dir else []
        dest = (self.cfg.get("packaging") or {}).get("copy_to_folder") or ""
        copied = dest if psarcs and dest and (Path(dest) / psarcs[-1].name).exists() else ""
        ok = code == 0 and out_dir is not None
        self.write({"type": "done", "ok": ok, "code": code, "outDir": str(out_dir or ""),
                    "psarc": str(psarcs[-1]) if psarcs else "", "copiedTo": copied,
                    "checks": report_checks(out_dir / "report.txt") if out_dir else [],
                    "error": "" if ok else (last_error or f"make_cdlc stopped (code {code})")})

    def open_folder(self, msg: dict) -> None:
        if msg.get("what") == "dlc":
            target = Path((self.cfg.get("packaging") or {}).get("copy_to_folder") or "")
        else:
            base = songs_dir(self.cfg) / safe_name(msg.get("folder", ""))
            outs = sorted((base / "output").glob("*/"), key=lambda p: p.stat().st_mtime)
            target = outs[-1] if outs else base
        if target and target.exists() and sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 - opens Explorer at a folder we chose
        self.write({"type": "opened", "path": str(target)})


def main() -> int:
    _binary_stdio()
    host = Host()
    while True:
        try:
            msg = read_message()
        except Exception as e:  # noqa: BLE001
            send({"type": "done", "ok": False, "error": f"bad message: {e}"})
            return 1
        if msg is None:
            return 0
        try:
            host.handle(msg)
        except Exception as e:  # noqa: BLE001
            send({"type": "done", "ok": False, "error": str(e)})


if __name__ == "__main__":
    sys.exit(main())
