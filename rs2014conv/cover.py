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
