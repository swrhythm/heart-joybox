"""The bytes that reach the printer."""

import pytest
from PIL import Image

from joybox import raster
from joybox.raster import RenderOptions


def test_single_black_pixel_sets_the_top_left_bit():
    image = Image.new("RGB", (16, 1), "white")
    image.putpixel((0, 0), (0, 0, 0))
    data = raster.render(image, RenderOptions(width_dots=16)).data
    #      GS  v   0  m | xL xH | yL yH | payload
    assert data == b"\x1dv0\x00" + b"\x02\x00" + b"\x01\x00" + b"\x80\x00"


def bands(data: bytes) -> list[tuple[int, int, bytes]]:
    """Unpack a job into (stride, rows, payload) so tests can read it."""
    out, offset = [], 0
    while offset < len(data):
        assert data[offset:offset + 4] == b"\x1dv0\x00"
        stride = int.from_bytes(data[offset + 4:offset + 6], "little")
        rows = int.from_bytes(data[offset + 6:offset + 8], "little")
        start = offset + 8
        out.append((stride, rows, data[start:start + stride * rows]))
        offset = start + stride * rows
    return out


def test_bands_split_at_the_configured_height():
    image = Image.new("RGB", (8, 5), "white")
    data = raster.render(image, RenderOptions(width_dots=8, band_height=2)).data
    assert [(stride, rows) for stride, rows, _ in bands(data)] == [(1, 2), (1, 2), (1, 1)]
    assert sum(rows for _, rows, _ in bands(data)) == 5


def test_width_is_padded_to_a_whole_number_of_bytes():
    image = Image.new("RGB", (12, 1), "black")
    rendered = raster.render(image, RenderOptions(width_dots=12))
    assert rendered.width == 16
    # Content is centred in the padded row, and the padding dots are white -
    # an undefined padding bit would print as a black smear down one edge.
    assert rendered.data[-2:] == b"\x3f\xfc"


def test_threshold_is_sharp_and_dither_is_not():
    grey = Image.new("RGB", (64, 64), (140, 140, 140))
    light = raster.render(grey, RenderOptions(width_dots=64, threshold=128)).data
    dark = raster.render(grey, RenderOptions(width_dots=64, threshold=200)).data
    assert set(light[8:]) == {0x00}                # above threshold -> all white
    assert set(dark[8:]) == {0xFF}                 # below threshold -> all black
    dithered = raster.render(grey, RenderOptions(width_dots=64, dither=True)).data
    assert len(set(dithered[8:])) > 1


def test_images_are_scaled_to_the_print_width():
    rendered = raster.render(Image.new("RGB", (1200, 600), "white"),
                             RenderOptions(width_dots=576))
    assert rendered.width == 576
    assert rendered.height == 288


def test_an_oversized_image_is_shrunk_instead_of_eating_the_roll():
    rendered = raster.render(Image.new("RGB", (576, 9000), "white"),
                             RenderOptions(width_dots=576, max_height=3000))
    assert rendered.downscaled
    assert rendered.height == 3000
    assert rendered.width == 576                   # padded back out to full width


def test_transparency_lands_on_white_not_black():
    image = Image.new("RGBA", (8, 1), (0, 0, 0, 0))
    data = raster.render(image, RenderOptions(width_dots=8)).data
    assert data.endswith(b"\x00")


def test_encode_rejects_a_bitmap_it_cannot_pack():
    with pytest.raises(ValueError):
        raster.encode(Image.new("L", (8, 1)))
    with pytest.raises(ValueError):
        raster.encode(Image.new("1", (12, 1)))
