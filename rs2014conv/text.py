"""Text helpers for titles/artists in other scripts (e.g. Japanese)."""

from __future__ import annotations

import re
import unicodedata


def is_latin(text: str) -> bool:
    return all(ord(c) < 0x250 or unicodedata.category(c).startswith(("P", "Z", "S")) for c in text)


JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff66-\uff9f]")


def has_japanese(text: str) -> bool:
    return bool(text and JAPANESE.search(text))


def display_names(meta: dict) -> dict:
    """Japanese title / artist / album -> romanized names for the song list (so the song is easy
    to find: "Rokudenashi - Yoake To Hotaru"), with the originals in the Japanese name fields,
    like official Japanese songs. Changes and returns `meta`."""
    for key, jp_key in (("title", "japanese_title"), ("artist", "japanese_artist"), ("album", None)):
        value = meta.get(key) or ""
        if not has_japanese(value):
            continue
        roman = romanize(value)
        if not roman or has_japanese(roman):
            continue   # (pykakasi missing: keep the original)
        if jp_key:
            meta[jp_key] = value
        meta[key] = roman
    return meta


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
