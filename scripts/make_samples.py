#!/usr/bin/env python3
"""Generate the placeholder artwork that ships in content-template/.

These exist so the very first button press produces a real receipt before any
artwork has been made.  Replace them with your own PNGs; nothing in the code
depends on what they contain.

    python3 scripts/make_samples.py --width 576 --out content-template
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from joybox.selftest import load_font  # noqa: E402

MARGIN = 24

VERSES = [
    ("For God so loved the world, that he gave his only begotten Son, that "
     "whosoever believeth in him should not perish, but have everlasting life.",
     "John 3:16"),
    ("The LORD is my shepherd; I shall not want. He maketh me to lie down in "
     "green pastures: he leadeth me beside the still waters.",
     "Psalm 23:1-2"),
    ("I can do all things through Christ which strengtheneth me.",
     "Philippians 4:13"),
    ("For I know the thoughts that I think toward you, saith the LORD, thoughts "
     "of peace, and not of evil, to give you an expected end.",
     "Jeremiah 29:11"),
]


def wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    words = text.split()
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def canvas(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("L", (width, height), 255)
    return image, ImageDraw.Draw(image)


def make_header(width: int) -> Image.Image:
    image, draw = canvas(width, 260)
    inner = width - 2 * MARGIN
    title = load_font(46, bold=True)
    sub = load_font(20)

    y = 18
    draw.text((width // 2, y), "HEART JOYBOX", font=title, fill=0, anchor="mt")
    y += 58
    draw.line((MARGIN, y, width - MARGIN, y), fill=0, width=3)
    y += 18
    for line in wrap(draw, "Welcome, friend. Here is a word for you today.", sub, inner):
        draw.text((width // 2, y), line, font=sub, fill=0, anchor="mt")
        y += 26
    y += 6
    draw.line((MARGIN, y, width - MARGIN, y), fill=0, width=1)
    return image.crop((0, 0, width, y + 16))


def make_body(width: int, verse: str, reference: str) -> Image.Image:
    image, draw = canvas(width, 700)
    inner = width - 2 * MARGIN
    body = load_font(28)
    ref = load_font(24, bold=True)

    y = 20
    for line in wrap(draw, verse, body, inner):
        draw.text((width // 2, y), line, font=body, fill=0, anchor="mt")
        y += 38
    y += 12
    draw.text((width // 2, y), reference, font=ref, fill=0, anchor="mt")
    return image.crop((0, 0, width, y + 44))


def make_footer(width: int) -> Image.Image:
    image, draw = canvas(width, 460)
    inner = width - 2 * MARGIN
    small = load_font(20)
    handle = load_font(26, bold=True)

    y = 10
    draw.line((MARGIN, y, width - MARGIN, y), fill=0, width=1)
    y += 20
    for line in wrap(draw, "You are seen, you are loved, and you are not alone.", small, inner):
        draw.text((width // 2, y), line, font=small, fill=0, anchor="mt")
        y += 26
    y += 10

    # Placeholder for the real QR code - see docs/CONTENT.md for the size rules.
    box = 200
    left = (width - box) // 2
    for offset in range(0, box, 16):
        draw.line((left + offset, y, left + offset + 8, y), fill=0, width=2)
        draw.line((left + offset, y + box, left + offset + 8, y + box), fill=0, width=2)
        draw.line((left, y + offset, left, y + offset + 8), fill=0, width=2)
        draw.line((left + box, y + offset, left + box, y + offset + 8), fill=0, width=2)
    draw.text((width // 2, y + box // 2 - 16), "YOUR QR", font=handle, fill=0, anchor="mm")
    draw.text((width // 2, y + box // 2 + 14), "CODE HERE", font=handle, fill=0, anchor="mm")
    y += box + 22

    draw.text((width // 2, y), "@your_instagram", font=handle, fill=0, anchor="mt")
    y += 34
    draw.text((width // 2, y), "replace footer.png with your own design",
              font=small, fill=0, anchor="mt")
    y += 30
    draw.line((MARGIN, y, width - MARGIN, y), fill=0, width=3)
    return image.crop((0, 0, width, y + 14))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=576, help="print width in dots")
    parser.add_argument("--out", type=Path, default=Path("content-template"))
    args = parser.parse_args()

    body_dir = args.out / "body"
    body_dir.mkdir(parents=True, exist_ok=True)

    make_header(args.width).save(args.out / "header.png")
    make_footer(args.width).save(args.out / "footer.png")
    for index, (verse, reference) in enumerate(VERSES, start=1):
        make_body(args.width, verse, reference).save(body_dir / f"{index}.png")

    print(f"wrote sample artwork to {args.out} at {args.width} dots wide")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
