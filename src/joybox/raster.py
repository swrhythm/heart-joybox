"""Turn an image file into ESC/POS raster bytes.

The Pi Zero is slow, so this runs ahead of time (see :mod:`joybox.cache`) and a
button press only has to blit the finished bytes at the printer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from . import escpos

log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

# 1 in PIL mode "1" is white; 1 in an ESC/POS raster is a fired dot.
_INVERT = bytes(255 - i for i in range(256))


@dataclass(frozen=True)
class RenderOptions:
    width_dots: int = 576
    dither: bool = False
    threshold: int = 128
    max_height: int = 3000
    band_height: int = 128

    def cache_key(self) -> str:
        return (
            f"w{self.width_dots}-{'d' if self.dither else f't{self.threshold}'}"
            f"-h{self.max_height}-b{self.band_height}"
        )


@dataclass(frozen=True)
class Rendered:
    data: bytes
    width: int
    height: int
    downscaled: bool = False

    def __len__(self) -> int:
        return len(self.data)


def _flatten(image: Image.Image) -> Image.Image:
    """Drop any alpha onto white and normalise orientation."""
    image = ImageOps.exif_transpose(image) or image
    if image.mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
    if image.mode in ("RGBA", "LA"):
        backdrop = Image.new("RGB", image.size, "white")
        backdrop.paste(image, mask=image.getchannel("A"))
        return backdrop
    return image.convert("RGB") if image.mode != "L" else image


def prepare(image: Image.Image, options: RenderOptions) -> tuple[Image.Image, bool]:
    """Scale, threshold and pad an image into a printable 1-bit bitmap.

    The returned width is always ``options.width_dots`` rounded up to a whole
    number of bytes, so the packed rows have no undefined padding bits.
    """
    image = _flatten(image)

    target_width = options.width_dots
    if image.width != target_width:
        height = max(1, round(image.height * target_width / image.width))
        image = image.resize((target_width, height), Image.Resampling.LANCZOS)

    # One oversized file must not swallow the whole paper roll.
    downscaled = False
    if image.height > options.max_height:
        downscaled = True
        width = max(8, round(image.width * options.max_height / image.height))
        image = image.resize((width, options.max_height), Image.Resampling.LANCZOS)
        log.warning(
            "image is taller than max_image_height (%d px); scaled down to %dx%d",
            options.max_height, image.width, image.height,
        )

    grey = image.convert("L")
    if options.dither:
        bitmap = grey.convert("1", dither=Image.Dither.FLOYDSTEINBERG)
    else:
        bitmap = grey.point(lambda p: 255 if p >= options.threshold else 0).convert(
            "1", dither=Image.Dither.NONE
        )

    stride_width = (target_width + 7) // 8 * 8
    if bitmap.size != (stride_width, bitmap.height):
        canvas = Image.new("1", (stride_width, bitmap.height), 1)  # 1 = white
        canvas.paste(bitmap, ((stride_width - bitmap.width) // 2, 0))
        bitmap = canvas
    return bitmap, downscaled


def encode(bitmap: Image.Image, band_height: int = 128) -> bytes:
    """Pack a 1-bit bitmap into ``GS v 0`` bands.

    Bands keep each command inside a small printer's input buffer; one giant
    raster is a reliable way to make a cheap printer stall mid-receipt.
    """
    if bitmap.mode != "1":
        raise ValueError(f"expected a 1-bit bitmap, got mode {bitmap.mode!r}")
    width, height = bitmap.size
    if width % 8:
        raise ValueError(f"bitmap width {width} is not a whole number of bytes")

    stride = width // 8
    rows = bitmap.tobytes().translate(_INVERT)

    out = bytearray()
    for top in range(0, height, band_height):
        count = min(band_height, height - top)
        out += escpos.raster_band(stride, count, rows[top * stride:(top + count) * stride])
    return bytes(out)


def render(image: Image.Image, options: RenderOptions) -> Rendered:
    bitmap, downscaled = prepare(image, options)
    return Rendered(
        data=encode(bitmap, options.band_height),
        width=bitmap.width,
        height=bitmap.height,
        downscaled=downscaled,
    )


def render_file(path: Path, options: RenderOptions) -> Rendered:
    """Render one image file.  Raises OSError / PIL errors for a bad file."""
    with Image.open(path) as image:
        image.load()
        return render(image, options)


def preview(path: Path, options: RenderOptions) -> Image.Image:
    """Exactly what the printer will put on paper, as a viewable image."""
    with Image.open(path) as image:
        image.load()
        bitmap, _ = prepare(image, options)
    return bitmap
