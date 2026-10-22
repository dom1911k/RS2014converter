"""Text helpers for titles/artists in other scripts (e.g. Japanese)."""

from __future__ import annotations

import re
import unicodedata


def is_latin(text: str) -> bool:
    return all(ord(c) < 0x250 or unicodedata.category(c).startswith(("P", "Z", "S")) for c in text)


def romanize(text: str) -> str:
    """Latin-letter version of a title/artist: Japanese via pykakasi (Hepburn), accents removed.

    Used where only Latin letters work: sort names, the DLC key, Guitar Pro files.
    """
    if not text:
        return text
    if not is_latin(text):
        try:
            import pykakasi

            parts = [p["hepburn"] for p in pykakasi.kakasi().convert(text)]
            text = " ".join(p for p in parts if p.strip())
            text = re.sub(r"\s+([.,!?。、])", r"\1", text)
            text = " ".join(w[:1].upper() + w[1:] for w in text.split())
        except ImportError:
            pass
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", text).strip()
