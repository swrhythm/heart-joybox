"""Find the printable images and choose which body image comes next."""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .raster import SUPPORTED_SUFFIXES

log = logging.getLogger(__name__)

# Copying onto a FAT32 card from a Mac or Windows laptop leaves these behind.
# Handing one to the printer prints nothing and looks like a broken kiosk.
JUNK_NAMES = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})
JUNK_DIRS = frozenset({"__macosx", ".spotlight-v100", ".trashes", ".fseventsd", "system volume information"})

_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """Sort 2.png before 10.png, the way a human numbers files."""
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in _DIGITS.split(name)
    )


def is_junk(path: Path) -> bool:
    name = path.name
    lowered = name.lower()
    if name.startswith("._") or lowered in JUNK_NAMES or name.startswith("."):
        return True
    return any(part.lower() in JUNK_DIRS for part in path.parts)


@dataclass(frozen=True)
class ContentSet:
    directory: Path
    header: Path | None = None
    footer: Path | None = None
    bodies: tuple[Path, ...] = ()
    problems: tuple[str, ...] = field(default=())

    @property
    def ready(self) -> bool:
        """A print needs at least one body image; header and footer are optional."""
        return bool(self.bodies)

    def all_images(self) -> list[Path]:
        return [p for p in (self.header, self.footer, *self.bodies) if p is not None]

    def fingerprint(self) -> tuple:
        """Changes whenever a file is added, removed, or edited."""
        marks = []
        for path in self.all_images():
            try:
                stat = path.stat()
                marks.append((str(path), stat.st_size, stat.st_mtime_ns))
            except OSError:
                marks.append((str(path), -1, -1))
        return tuple(marks)


def _usable(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def scan(directory: Path, header: str = "header.png", footer: str = "footer.png",
         body_dir: str = "body") -> ContentSet:
    """Inspect the content directory.  Never raises; missing pieces are reported."""
    problems: list[str] = []

    if not directory.is_dir():
        return ContentSet(
            directory=directory,
            problems=(f"content folder {directory} does not exist",),
        )

    def optional(name: str, label: str) -> Path | None:
        path = directory / name
        if _usable(path):
            return path
        if path.exists():
            problems.append(f"{label} {path} is empty; skipping it")
        else:
            problems.append(f"no {label} at {path}; receipts will print without it")
        return None

    body_root = directory / body_dir
    bodies: list[Path] = []
    if body_root.is_dir():
        for entry in body_root.iterdir():
            if is_junk(entry):
                continue
            if entry.suffix.lower() not in SUPPORTED_SUFFIXES:
                if entry.is_file():
                    problems.append(f"ignoring {entry.name}: not an image file")
                continue
            if not _usable(entry):
                problems.append(f"ignoring {entry.name}: empty file")
                continue
            bodies.append(entry)
        bodies.sort(key=lambda p: natural_key(p.name))
        if not bodies:
            problems.append(f"no images in {body_root}; nothing to print")
    else:
        problems.append(f"body folder {body_root} does not exist")

    return ContentSet(
        directory=directory,
        header=optional(header, "header image"),
        footer=optional(footer, "footer image"),
        bodies=tuple(bodies),
        problems=tuple(problems),
    )


class ShuffleBag:
    """Draw every image once before any repeats.

    Plain random choice visibly repeats itself, which reads as a broken kiosk to
    someone who presses twice.  The bag also refuses to open a fresh round with
    the image that just printed.
    """

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self._bag: list[Path] = []
        self._source: tuple[Path, ...] = ()
        self.last: Path | None = None

    def pick(self, items: Sequence[Path]) -> Path:
        if not items:
            raise ValueError("no images to choose from")
        current = tuple(items)
        if current != self._source:
            self._source = current
            self._bag = []
        self._bag = [p for p in self._bag if p in current]
        if not self._bag:
            self._bag = list(current)
            self._rng.shuffle(self._bag)
            # Avoid printing the same image twice across a bag boundary.
            if len(self._bag) > 1 and self._bag[-1] == self.last:
                swap = self._rng.randrange(len(self._bag) - 1)
                self._bag[-1], self._bag[swap] = self._bag[swap], self._bag[-1]
        self.last = self._bag.pop()
        return self.last
