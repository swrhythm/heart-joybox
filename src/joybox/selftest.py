"""The self-test page.

Its real job is answering one question you cannot answer from a datasheet:
is this printer 576 dots wide or 512?  The ruler runs to the configured width,
so if its right-hand end is missing from the paper, the width is wrong.
"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import __version__

FONT_PATHS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
)


def load_font(size: int, bold: bool = False):
    """A readable font, whatever is installed."""
    for path in FONT_PATHS:
        candidate = Path(path)
        if bold and "Bold" not in path:
            bold_variant = Path(path.replace("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"))
            candidate = bold_variant if bold_variant.exists() else candidate
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def build(width: int = 576) -> Image.Image:
    height = 520
    page = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(page)
    title = load_font(34, bold=True)
    body = load_font(20)
    small = load_font(15)

    y = 10
    draw.text((width // 2, y), "HEART JOYBOX", font=title, fill=0, anchor="mt")
    y += 42
    draw.text((width // 2, y), "SELF TEST", font=body, fill=0, anchor="mt")
    y += 34
    draw.line((0, y, width - 1, y), fill=0, width=2)
    y += 16

    draw.text((0, y), f"version {__version__}", font=small, fill=0)
    draw.text((width, y), time.strftime("%Y-%m-%d %H:%M"), font=small, fill=0, anchor="ra")
    y += 30

    # Width ruler: ticks every 64 dots, and a bar that ends at the last dot.
    draw.text((0, y), f"print width: {width} dots", font=body, fill=0)
    y += 28
    ruler_top = y
    draw.rectangle((0, ruler_top, width - 1, ruler_top + 14), fill=0)
    y += 20
    for x in range(0, width + 1, 64):
        tick = min(x, width - 1)
        draw.line((tick, y, tick, y + 10), fill=0, width=2)
        label = str(x)
        anchor = "la" if x == 0 else ("ra" if x >= width else "ma")
        draw.text((tick, y + 12), label, font=small, fill=0, anchor=anchor)
    y += 34

    draw.text((0, y), "The black bar must touch both edges of the paper.",
              font=small, fill=0)
    y += 20
    draw.text((0, y), "If its right end is missing, set width_dots = 512", font=small, fill=0)
    y += 20
    draw.text((0, y), "in config.toml on the SD card.", font=small, fill=0)
    y += 30

    # Grey ramp.  It goes through the same render path as your artwork, so it
    # shows exactly what happens to greys at the current threshold setting.
    draw.text((0, y), "grey ramp (dark to light)", font=small, fill=0)
    y += 18
    steps = 8
    step_width = width // steps
    for index in range(steps):
        shade = int(230 * index / (steps - 1))
        draw.rectangle((index * step_width, y, (index + 1) * step_width - 2, y + 28), fill=shade)
    y += 32
    draw.text((0, y), "With dither off, every grey lands on black or white.",
              font=small, fill=0)
    y += 26

    draw.text((0, y), "abcdefghijklmnopqrstuvwxyz 0123456789", font=small, fill=0)
    y += 22
    draw.text((0, y), "If you can read this line, the resolution is right.",
              font=small, fill=0)
    y += 30
    draw.line((0, y, width - 1, y), fill=0, width=2)
    y += 10
    draw.text((width // 2, y), "the paper should now be cut", font=small, fill=0, anchor="mt")

    return page.crop((0, 0, width, min(height, y + 30)))
