"""Default album art when none is given."""

from __future__ import annotations

import colorsys
import hashlib
import textwrap
from pathlib import Path


def make_cover(path: Path, title: str, artist: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    size = 512
    h = int(hashlib.md5(f"{artist}{title}".encode()).hexdigest()[:4], 16) / 0xFFFF
    top = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(h, 0.6, 0.55))
    bottom = tuple(int(c * 255) for c in colorsys.hsv_to_rgb((h + 0.12) % 1, 0.7, 0.2))
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        a = y / (size - 1)
        d.line([(0, y), (size, y)], fill=tuple(int(top[i] * (1 - a) + bottom[i] * a) for i in range(3)))

    def font(px):
        for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, px)
            except OSError:
                continue
        return ImageFont.load_default()

    y = 150
    for line in textwrap.wrap(title, 16)[:4]:
        f = font(48)
        w = d.textlength(line, font=f)
        d.text(((size - w) / 2, y), line, font=f, fill=(255, 255, 255))
        y += 60
    f = font(30)
    for line in textwrap.wrap(artist, 24)[:2]:
        w = d.textlength(line, font=f)
        d.text(((size - w) / 2, y + 20), line, font=f, fill=(230, 230, 230))
        y += 40
    img.save(path)


# --------------------------------------------------------------------------- album art online

# Versions that are not the original recording (unless the title asks for them).
_VERSION_WORDS = ("remix", "piano", "cover", "live", "acoustic", "instrumental", "karaoke", "remaster", "version",
                  "mix", "edit", "demo", "orchestral", "8-bit", "tribute", "concert", "cast recording", "symphon",
                  "lullaby", "greatest hits", "best of", "box set", "anniversary", "collection", "lofi", "lo-fi",
                  "chill", "tribute")


MIN_SCORE = 0.7   # a clear match: the right title and artist, at most one "version" word


def _norm(s: str) -> str:
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)|\[.*?\]|feat\..*$|ft\..*$", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _artist_parts(name: str) -> list[str]:
    import re

    return [p for p in (_norm(x) for x in re.split(r"\s*(?:&|,|/|\band\b|\bwith\b|\bx\b|\bvs\.?)\s*", name, flags=re.I)) if p]


def score_match(title: str, artist: str, album: str, r_title: str, r_artist: str, r_album: str) -> float:
    """How well a search result fits the song (0 = not it). Pure; exported for tests.

    The title must match (ignoring parts in brackets). The artist must be the result's
    artist (or one of several, "A & B"); a longer name that merely contains it is another
    artist ("Celtic Pink Floyd"). If the artist isn't in the result's artist at all but in its
    album title, it's a soundtrack-style credit (Songsterr lists "Persona 5" for songs by Lyn
    on the Persona 5 soundtrack). Remixes, live and piano versions, covers, compilations and
    other soundtracks count less unless the title asks for them.
    """
    from difflib import SequenceMatcher

    t, rt = _norm(title), _norm(r_title)
    if not t or not rt:
        return 0.0
    ts = SequenceMatcher(None, t, rt).ratio()
    if ts < 0.8 and not (rt.startswith(t + " ") or rt == t):
        return 0.0
    a = _norm(artist)
    ra, ral = _norm(r_artist), _norm(r_album)
    via_album = False
    if not a:
        asc = 0.5
    elif a == ra or a in _artist_parts(r_artist) or max(SequenceMatcher(None, a, p).ratio() for p in _artist_parts(r_artist) or [ra]) >= 0.92:
        asc = 1.0
    elif a in ra:
        return 0.0          # "pink floyd" in "celtic pink floyd": a different artist
    elif ral.startswith(a + " ") or ral == a:   # "Persona 5 (Original Soundtrack)", not "Music from Persona 5"
        asc, via_album = 0.8, True
    else:
        return 0.0
    full = f"{r_title} {r_album}".lower()
    wanted = title.lower() + " " + (album or "").lower()
    words = _VERSION_WORDS + (() if via_album else ("soundtrack", "music from", "the album", "motion picture"))
    penalty = sum(0.25 for w in words if w in full and w not in wanted)
    bonus = 0.1 if album and _norm(album) and _norm(album) in ral else 0.0
    return max(0.0, ts * asc - min(0.6, penalty) + bonus)


def _get_json(url: str, timeout: float):
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "RS2014converter (album art lookup)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get_bytes(url: str, timeout: float) -> bytes:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "RS2014converter (album art lookup)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def cover_candidates(title: str, artist: str, album: str = "", timeout: float = 8.0) -> list[tuple[float, str, str]]:
    """[(score, image url, description)] from iTunes and Deezer, best first; among equally good
    matches the earliest release (usually the original album, not a later compilation)."""
    from urllib.parse import quote

    out = []
    term = quote(f"{artist} {title}".strip())
    try:
        d = _get_json(f"https://itunes.apple.com/search?term={term}&entity=song&limit=50", timeout)
        for r in d.get("results", []):
            s = score_match(title, artist, album, r.get("trackName", ""), r.get("artistName", ""), r.get("collectionName", ""))
            if s > 0 and r.get("artworkUrl100"):
                url = r["artworkUrl100"].replace("100x100bb", "1000x1000bb")
                out.append((s, url, f"iTunes: {r.get('artistName')} - {r.get('collectionName')}", r.get("releaseDate") or "9999"))
    except Exception:  # noqa: BLE001 - offline or the service changed: try the next one
        pass
    if not any(x[0] >= 0.95 for x in out):   # nothing clearly right on iTunes: ask Deezer too
        try:
            d = _get_json(f"https://api.deezer.com/search?q={term}&limit=50", timeout)
            for r in d.get("data", []):
                s = score_match(title, artist, album, r.get("title", ""), (r.get("artist") or {}).get("name", ""),
                                (r.get("album") or {}).get("title", ""))
                cover_url = (r.get("album") or {}).get("cover_xl")
                if s > 0 and cover_url:
                    out.append((s, cover_url, f"Deezer: {(r.get('artist') or {}).get('name')} - {(r.get('album') or {}).get('title')}", "9999"))
        except Exception:  # noqa: BLE001
            pass
    out = [x for x in out if x[0] >= MIN_SCORE]   # no cover is better than a wrong one
    out.sort(key=lambda x: (-round(x[0], 1), x[3]))   # then the service's own order (stable sort)
    return [(sc, url, desc) for sc, url, desc, _ in out]


def download_cover(path: Path, title: str, artist: str, album: str = "", timeout: float = 8.0) -> str | None:
    """Save the album cover found online as a 512x512 image at `path`; returns where it came from."""
    from io import BytesIO

    from PIL import Image, ImageOps

    for score, url, desc in cover_candidates(title, artist, album, timeout)[:3]:
        try:
            img = Image.open(BytesIO(_get_bytes(url, timeout))).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        if min(img.size) < 200:
            continue
        ImageOps.fit(img, (512, 512), Image.LANCZOS).save(path)
        return desc
    return None
