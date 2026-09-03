"""Talking to the printer over its USB character device.

The printer is reached through the kernel's ``usblp`` driver as
``/dev/usb/lp0`` (given the stable name ``/dev/joybox-printer`` by our udev
rule).  That beats driving libusb directly: no kernel driver to detach, no
special permissions past group ``lp``, and unplugging or power-cycling the
printer just makes the next write fail so we can reopen.

Every wait has a deadline.  A wedged printer must surface as an error the LED
can show, never as a hung process the watchdog has to reboot.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import select
import struct
import time
from pathlib import Path
from typing import Iterable

from . import escpos
from .escpos import PrinterStatus

log = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 15.0


class TransportError(RuntimeError):
    """The printer could not be reached or did not accept the data."""


class Transport:
    """Interface shared by the real device and the test/dry-run stand-ins."""

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def status(self) -> PrinterStatus:
        return PrinterStatus()

    def close(self) -> None:
        pass

    def describe(self) -> str:
        return self.__class__.__name__


class CharDeviceTransport(Transport):
    def __init__(
        self,
        device: str | Path,
        fallbacks: Iterable[str | Path] = (),
        chunk_bytes: int = 4096,
        pause_seconds: float = 0.0,
        write_timeout_seconds: float = 30.0,
        status_timeout_seconds: float = 0.4,
    ):
        self.candidates = [Path(device), *(Path(p) for p in fallbacks)]
        self.chunk_bytes = max(64, chunk_bytes)
        self.pause_seconds = pause_seconds
        self.write_timeout_seconds = write_timeout_seconds
        self.status_timeout_seconds = status_timeout_seconds
        self._fd: int | None = None
        self._path: Path | None = None
        self._readable = False
        self._failures = 0
        self._retry_after = 0.0

    # ------------------------------------------------------------ opening

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    @property
    def device_path(self) -> Path | None:
        return self._path

    def find_device(self) -> Path | None:
        for candidate in self.candidates:
            if candidate.exists():
                return candidate
        return None

    def open(self, force: bool = False) -> None:
        """Open the device, honouring the backoff unless ``force``."""
        if self._fd is not None:
            return
        now = time.monotonic()
        if not force and now < self._retry_after:
            raise TransportError("waiting before retrying the printer connection")

        path = self.find_device()
        if path is None:
            self._note_failure()
            raise TransportError(
                f"no printer device found (looked for {', '.join(str(p) for p in self.candidates)})"
            )

        # Read/write lets us ask the printer for status; write-only still prints.
        for flags, readable in ((os.O_RDWR, True), (os.O_WRONLY, False)):
            try:
                self._fd = os.open(path, flags | os.O_NONBLOCK)
                self._readable = readable
                break
            except OSError as exc:
                last = exc
        if self._fd is None:
            self._note_failure()
            hint = " (is the joybox user in the 'lp' group?)" if last.errno == errno.EACCES else ""
            raise TransportError(f"cannot open {path}: {last.strerror}{hint}") from last

        self._path = path
        self._failures = 0
        self._retry_after = 0.0
        log.info("printer opened at %s (%s)", path, "read/write" if self._readable else "write-only")

    def _note_failure(self) -> None:
        self._failures += 1
        backoff = min(MAX_BACKOFF_SECONDS, 2.0 ** min(self._failures, 4))
        self._retry_after = time.monotonic() + backoff

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd = None
        self._path = None
        self._readable = False

    def describe(self) -> str:
        if self._path is not None:
            return f"{self._path} ({'read/write' if self._readable else 'write-only'})"
        found = self.find_device()
        return str(found) if found else "not connected"

    # ------------------------------------------------------------ writing

    def write(self, data: bytes) -> None:
        """Send bytes to the printer, reopening once if the link dropped."""
        try:
            self._write_once(data)
        except TransportError:
            # A printer that was power-cycled mid-session fails exactly once.
            self.close()
            self._write_once(data)

    def _write_once(self, data: bytes) -> None:
        self.open(force=True)
        assert self._fd is not None
        deadline = time.monotonic() + self.write_timeout_seconds
        view = memoryview(data)
        sent = 0
        while sent < len(view):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise TransportError(
                    f"printer stopped accepting data after {sent} of {len(view)} bytes "
                    "(out of paper, cover open, or powered off?)"
                )
            ready = select.select([], [self._fd], [], min(remaining, 1.0))[1]
            if not ready:
                continue
            chunk = view[sent:sent + self.chunk_bytes]
            try:
                sent += os.write(self._fd, chunk)
            except BlockingIOError:
                continue
            except OSError as exc:
                self.close()
                self._note_failure()
                raise TransportError(f"write to printer failed: {exc.strerror}") from exc
            if self.pause_seconds:
                time.sleep(self.pause_seconds)

    # ------------------------------------------------------------- status

    def port_status(self) -> PrinterStatus:
        """USB printer-class port status - no round-trip to the printer."""
        if self._fd is None:
            return PrinterStatus()
        try:
            raw = fcntl.ioctl(self._fd, escpos.LPGETSTATUS, struct.pack("i", 0), True)
        except OSError as exc:
            log.debug("LPGETSTATUS unavailable: %s", exc)
            return PrinterStatus()
        value = struct.unpack("i", raw)[0] & 0xFF
        if value == 0:
            return PrinterStatus()  # driver had nothing to report
        return escpos.decode_port_status(value)

    def query(self, command: bytes) -> int | None:
        """Send a real-time status command and read one byte back."""
        if self._fd is None or not self._readable or self.status_timeout_seconds <= 0:
            return None
        self._drain()
        try:
            os.write(self._fd, command)
        except OSError as exc:
            log.debug("status command failed: %s", exc)
            return None
        ready = select.select([self._fd], [], [], self.status_timeout_seconds)[0]
        if not ready:
            return None
        try:
            reply = os.read(self._fd, 1)
        except OSError:
            return None
        return reply[0] if reply else None

    def _drain(self) -> None:
        """Throw away any unread reply so we do not answer the wrong question."""
        assert self._fd is not None
        while select.select([self._fd], [], [], 0)[0]:
            try:
                if not os.read(self._fd, 64):
                    return
            except OSError:
                return

    def status(self) -> PrinterStatus:
        """Best-effort health check.  Unknown is not the same as broken."""
        try:
            self.open()
        except TransportError as exc:
            return PrinterStatus(online=False, source="disconnected", raw=None) if \
                self.find_device() is None else PrinterStatus(online=False, source=str(exc))

        reads = [self.port_status()]
        paper = self.query(escpos.STATUS_PAPER)
        if paper is not None:
            reads.append(escpos.decode_paper_status(paper))
        offline = self.query(escpos.STATUS_OFFLINE)
        if offline is not None:
            reads.append(escpos.decode_offline_status(offline))
        return escpos.merge(*reads)


class MemoryTransport(Transport):
    """Collects everything written, for tests and ``--dry-run``."""

    def __init__(self, status: PrinterStatus | None = None):
        self.buffer = bytearray()
        self.writes: list[bytes] = []
        self._status = status or PrinterStatus(online=True, source="memory")

    def write(self, data: bytes) -> None:
        self.buffer += data
        self.writes.append(bytes(data))

    def status(self) -> PrinterStatus:
        return self._status

    def describe(self) -> str:
        return "in-memory (dry run)"


class FileTransport(Transport):
    """Writes raw ESC/POS to a file, for inspecting a job off the Pi."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._handle = self.path.open("wb")

    def write(self, data: bytes) -> None:
        self._handle.write(data)

    def close(self) -> None:
        self._handle.close()

    def describe(self) -> str:
        return f"file {self.path}"
