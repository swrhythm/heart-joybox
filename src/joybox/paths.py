"""Filesystem locations.

Content lives on the SD card's FAT32 boot partition so it can be updated by
pulling the card and dragging files in on any Windows/Mac laptop.  Raspberry Pi
OS mounts that partition at /boot/firmware since Bookworm and at /boot before
that, so we probe rather than hardcode.
"""

from __future__ import annotations

import os
from pathlib import Path

BOOT_CANDIDATES = (Path("/boot/firmware"), Path("/boot"))
CONTENT_DIR_NAME = "heart-joybox"

ETC_CONFIG = Path("/etc/heart-joybox/config.toml")
CACHE_DIR = Path("/var/cache/heart-joybox")
STATE_DIR = Path("/var/lib/heart-joybox")


def boot_dir() -> Path:
    """Return the mounted boot partition, or the best guess if none is mounted."""
    override = os.environ.get("JOYBOX_BOOT_DIR")
    if override:
        return Path(override)
    for candidate in BOOT_CANDIDATES:
        # A real boot partition always has the firmware blobs next to it.
        if (candidate / "config.txt").exists() or (candidate / "cmdline.txt").exists():
            return candidate
    for candidate in BOOT_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return BOOT_CANDIDATES[0]


def content_dir() -> Path:
    """Directory holding config.toml, header.png, footer.png and body/."""
    override = os.environ.get("JOYBOX_CONTENT_DIR")
    if override:
        return Path(override)
    return boot_dir() / CONTENT_DIR_NAME


def boot_config() -> Path:
    return content_dir() / "config.toml"


def cache_dir() -> Path:
    return Path(os.environ.get("JOYBOX_CACHE_DIR", CACHE_DIR))
