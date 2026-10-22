"""DLC Builder project file (.rs2dlc) writer + stable IDs per song."""

from __future__ import annotations

import json
import random
import re
import uuid
from pathlib import Path


def sort_value(text: str) -> str:
    """Like DLC Builder: drop a leading 'The ', keep letters/digits/spaces."""
    t = re.sub(r"^(the)\s+", "", text.strip(), flags=re.I)
    t = re.sub(r"[^A-Za-z0-9 ]", "", t).strip()
    return t[:1].upper() + t[1:] if t else text


def dlc_key(charter: str, artist: str, title: str) -> str:
    def part(s: str, n: int) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", s)[:n]

    prefix = part(charter, 2) or "MC"
    a, t = part(artist, 5), part(title, 5)
    key = prefix + a + t
    if len(a) + len(t) < 6:
        # Titles/artists without Latin letters (e.g. Japanese) would all get the same key.
        import hashlib

        digest = hashlib.sha1(f"{artist}|{title}".encode("utf-8")).hexdigest()
        key += "".join(c for c in digest if c.isalpha())[:4].upper() + digest[:2]
    return key


def load_ids(path: Path, key: str) -> dict:
    """IDs must stay the same between rebuilds, otherwise Rocksmith treats it as a new song."""
    if path.exists():
        data = json.loads(path.read_text())
        if data.get("DLCKey"):
            return data
    data = {"DLCKey": key, "ArrangementID": str(uuid.uuid4()), "PersistentID": str(uuid.uuid4()),
            "MasterID": random.randint(10_000_000, 2_000_000_000)}
    path.write_text(json.dumps(data, indent=2))
    return data


def write(path: Path, *, ids: dict, meta: dict, audio: str, preview: str | None, preview_start: float | None,
          cover: str, xml: str, offsets: list[int], bass_pick: bool, tone_key: str, author: str,
          volume: float = -7.0, preview_volume: float = -7.0, tuning_pitch: float = 440.0) -> None:
    proj = {
        "Version": meta.get("version", "1"),
        "Author": author,
        "DLCKey": ids["DLCKey"],
        "ArtistName": {"Value": meta["artist"], "SortValue": meta["artist_sort"]},
        "Title": {"Value": meta["title"], "SortValue": meta["title_sort"]},
        "AlbumName": {"Value": meta["album"], "SortValue": meta["album_sort"]},
        "Year": int(meta["year"]),
        "AlbumArtFile": cover,
        "AudioFile": {"Path": audio, "Volume": volume},
        "AudioPreviewFile": {"Path": preview or "", "Volume": preview_volume},
        "Arrangements": [{
            "Case": "Instrumental",
            "Fields": [{
                "ID": ids["ArrangementID"],
                "XML": xml,
                "Name": 3,  # Bass
                "RouteMask": 4,  # Bass
                "Priority": 0,  # Main
                "ScrollSpeed": 1.3,
                "BassPicked": bass_pick,
                "Tuning": list(offsets[:6]),
                "TuningPitch": round(float(tuning_pitch), 3),
                "BaseTone": tone_key,
                "Tones": [],
                "MasterID": ids["MasterID"],
                "PersistentID": ids["PersistentID"],
            }],
        }],
        "Tones": [],
    }
    if preview_start is not None:
        proj["AudioPreviewStartTime"] = round(float(preview_start), 3)
    path.write_text(json.dumps(proj, indent=2, ensure_ascii=False), encoding="utf-8")
