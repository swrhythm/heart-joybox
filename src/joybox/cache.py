"""Cache of rendered ESC/POS bytes.

Rendering a full-width PNG on a Pi Zero takes long enough to be felt as dead
air after a button press, so every image is rendered once and the finished
bytes are kept on disk (keyed by content identity, so editing an image
invalidates it) and in memory.

If the cache directory is unusable - read-only root, full SD card - rendering
still works, it is just not persisted.
"""

from __future__ import annotations

import hashlib
import logging
import struct
import threading
from pathlib import Path

from . import paths, raster
from .raster import Rendered, RenderOptions

log = logging.getLogger(__name__)

MAGIC = b"JBX1"
HEADER = struct.Struct("<4sHIB")  # magic, width, height, flags
FLAG_DOWNSCALED = 0x01
MEMORY_BUDGET_BYTES = 8 * 1024 * 1024


def identity(path: Path, options: RenderOptions) -> str:
    """A key that changes whenever the file or the render settings change."""
    stat = path.stat()
    seed = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{options.cache_key()}"
    return hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:32]


class RenderCache:
    def __init__(self, options: RenderOptions, directory: Path | None = None):
        self.options = options
        self.directory = paths.cache_dir() if directory is None else directory
        self._memory: dict[str, Rendered] = {}
        self._memory_bytes = 0
        self._lock = threading.Lock()
        self.disk_enabled = self._ensure_directory()

    def _ensure_directory(self) -> bool:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            probe = self.directory / ".writable"
            probe.write_bytes(b"")
            probe.unlink()
            return True
        except OSError as exc:
            log.warning("render cache disabled, %s is not writable (%s)", self.directory, exc)
            return False

    # ---------------------------------------------------------------- disk

    def _read_disk(self, key: str) -> Rendered | None:
        if not self.disk_enabled:
            return None
        blob = self.directory / f"{key}.bin"
        try:
            raw = blob.read_bytes()
        except OSError:
            return None
        if len(raw) < HEADER.size:
            return None
        magic, width, height, flags = HEADER.unpack_from(raw)
        if magic != MAGIC:
            return None
        return Rendered(
            data=raw[HEADER.size:],
            width=width,
            height=height,
            downscaled=bool(flags & FLAG_DOWNSCALED),
        )

    def _write_disk(self, key: str, item: Rendered) -> None:
        if not self.disk_enabled:
            return
        blob = self.directory / f"{key}.bin"
        temp = blob.with_suffix(".tmp")
        flags = FLAG_DOWNSCALED if item.downscaled else 0
        try:
            temp.write_bytes(HEADER.pack(MAGIC, item.width, item.height, flags) + item.data)
            temp.replace(blob)
        except OSError as exc:
            log.warning("could not cache %s (%s)", blob, exc)
            self.disk_enabled = False

    # -------------------------------------------------------------- memory

    def _remember(self, key: str, item: Rendered) -> None:
        if self._memory_bytes + len(item) > MEMORY_BUDGET_BYTES:
            self._memory.clear()
            self._memory_bytes = 0
        self._memory[key] = item
        self._memory_bytes += len(item)

    # --------------------------------------------------------------- api

    def get(self, path: Path) -> Rendered:
        """Rendered bytes for one image, rendering it if we have not already."""
        key = identity(path, self.options)
        hit = self._memory.get(key)
        if hit is not None:
            return hit
        # Serialised so the start-up warm-up and a button press cannot render
        # the same image twice or trip over each other's bookkeeping.
        with self._lock:
            hit = self._memory.get(key)
            if hit is not None:
                return hit
            hit = self._read_disk(key)
            if hit is None:
                hit = raster.render_file(path, self.options)
                self._write_disk(key, hit)
            self._remember(key, hit)
        return hit

    def warm(self, files: list[Path]) -> tuple[int, int]:
        """Render everything up front.  Returns (rendered, failed)."""
        ok = failed = 0
        for path in files:
            try:
                self.get(path)
                ok += 1
            except (OSError, ValueError) as exc:
                failed += 1
                log.error("cannot render %s: %s", path, exc)
        return ok, failed

    def prune(self, files: list[Path]) -> int:
        """Delete cached renders that no live image maps to.  Returns count."""
        if not self.disk_enabled:
            return 0
        live = set()
        for path in files:
            try:
                live.add(f"{identity(path, self.options)}.bin")
            except OSError:
                continue
        removed = 0
        try:
            for blob in self.directory.glob("*.bin"):
                if blob.name not in live:
                    blob.unlink(missing_ok=True)
                    removed += 1
        except OSError as exc:
            log.warning("could not prune render cache (%s)", exc)
        return removed
