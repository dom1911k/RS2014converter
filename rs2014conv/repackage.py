"""Build only the .psarc again for a finished song (e.g. after installing Wwise), and update
its report.txt. Much faster than running make_cdlc again: the chart and audio are reused.

    python -m rs2014conv.repackage "<song folder>\\output\\<Artist - Title>"
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import config
from .pipeline import Report, _package

PACKAGING_CHECKS = ("Building the .psarc failed", "No .psarc was built", "Wwise 2023 wasn't found",
                    "Your Wwise is a version", "Wwise couldn't convert", "DLC Builder's chart checks")
NOTHING = "Nothing special to check - still listen to review\\check_mix.wav once."


def update_report(text: str, new: Report, psarc: Path | None = None) -> str:
    """report.txt with its packaging lines (and packaging checks) replaced by `new`'s."""
    lines = text.splitlines()
    # 1. The PLEASE CHECK box: keep the other checks, replace the packaging ones.
    checks, start, end = [], None, None
    for i, line in enumerate(lines):
        if line.startswith("PLEASE CHECK:") or line == NOTHING:
            start = i
            end = i + 1
            while end < len(lines) and lines[end].startswith("  * "):
                checks.append(lines[end][4:])
                end += 1
            break
    checks = [c for c in checks if not c.startswith(PACKAGING_CHECKS)] + new.attention
    box = ["PLEASE CHECK:"] + [f"  * {c}" for c in checks] if checks else [NOTHING]
    if start is not None:
        lines[start:end] = box
    # 2. The packaging section: drop the old one, append the new one.
    out, skipping = [], False
    for line in lines:
        if line.startswith("Packaging:"):
            skipping = True
            continue
        if skipping and (line.startswith("  ") or not line.strip()):
            continue
        skipping = False
        if psarc and line.startswith("  CDLC:"):
            line = f"  CDLC:        {psarc}"
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out + new.lines) + "\n"


def repackage(out_dir: Path) -> Path | None:
    out_dir = Path(out_dir)
    projects = sorted((out_dir / "project").glob("*.rs2dlc"))
    if not projects:
        print(f"No project (.rs2dlc) in {out_dir / 'project'}: make the CDLC again instead.")
        return None
    print("== Packaging", flush=True)
    rep = Report()
    psarc = _package(projects[0], out_dir, config.load(None), rep)
    report = out_dir / "report.txt"
    if report.exists():
        report.write_text(update_report(report.read_text(encoding="utf-8"), rep, psarc), encoding="utf-8")
    print("Done: " + str(psarc) if psarc else "The .psarc could not be built: " + (rep.attention[0] if rep.attention else "see above"))
    return psarc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(0 if repackage(Path(sys.argv[1])) else 1)
