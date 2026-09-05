import sys
import types
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def write_image(path: Path, width: int = 576, height: int = 40, colour: str = "white") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), colour).save(path)
    return path


def fake_gpiozero(**members):
    """A stand-in for the gpiozero module, which CI never installs."""
    return types.SimpleNamespace(**members)


@pytest.fixture
def content_dir(tmp_path: Path) -> Path:
    """A content folder shaped like the one on the SD card."""
    write_image(tmp_path / "header.png")
    write_image(tmp_path / "footer.png")
    for index in (1, 2, 3):
        write_image(tmp_path / "body" / f"{index}.png", height=60)
    return tmp_path
