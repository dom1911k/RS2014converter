"""Tiny console logger that also collects warnings for the final report."""

from __future__ import annotations

import sys

_warnings: list[str] = []


def info(msg: str) -> None:
    print(f"  {msg}", flush=True)


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def warn(msg: str) -> None:
    _warnings.append(msg)
    print(f"  WARNING: {msg}", file=sys.stdout, flush=True)


def collected_warnings() -> list[str]:
    return list(_warnings)
