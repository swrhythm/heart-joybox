"""Composing and sending a receipt."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import escpos
from .cache import RenderCache
from .config import Config
from .content import ContentSet, ShuffleBag
from .escpos import PrinterStatus
from .transport import Transport, TransportError

log = logging.getLogger(__name__)


class PrintBlocked(RuntimeError):
    """The printer told us it cannot print right now (no paper, cover open)."""

    def __init__(self, message: str, status: PrinterStatus):
        super().__init__(message)
        self.status = status


@dataclass
class PrintResult:
    body: Path | None = None
    sections: tuple[str, ...] = ()
    bytes_sent: int = 0
    seconds: float = 0.0
    skipped: tuple[str, ...] = field(default=())


class ReceiptPrinter:
    def __init__(self, config: Config, transport: Transport, cache: RenderCache,
                 bag: ShuffleBag | None = None):
        self.config = config
        self.transport = transport
        self.cache = cache
        self.bag = bag or ShuffleBag()

    # ------------------------------------------------------------- status

    def status(self, force: bool = False) -> PrinterStatus:
        if not self.config.printer.status_check:
            return PrinterStatus()
        try:
            return self.transport.status(force=force)
        except (TransportError, OSError) as exc:  # pragma: no cover - defensive
            log.warning("status check failed: %s", exc)
            return PrinterStatus()

    def _guard(self) -> PrinterStatus:
        # Forced: someone is standing there having just pressed the button.
        status = self.status(force=True)
        if status.blocked:
            raise PrintBlocked(f"printer is {status.describe()}", status)
        return status

    # ------------------------------------------------------------ receipts

    def trailer(self) -> bytes:
        """Feed the receipt clear of the cutter, then cut."""
        settings = self.config.printing
        return escpos.feed(settings.feed_lines_before_cut) + escpos.cut(settings.cut)

    def compose(self, images: list[Path]) -> tuple[bytes, list[str], list[str]]:
        """Build the job.  A single unreadable image is skipped, not fatal."""
        job = bytearray(escpos.INIT + escpos.ALIGN_LEFT)
        printed: list[str] = []
        skipped: list[str] = []
        for path in images:
            try:
                job += self.cache.get(path).data
                printed.append(path.name)
            except (OSError, ValueError) as exc:
                log.error("skipping %s: %s", path, exc)
                skipped.append(path.name)
        job += self.trailer()
        return bytes(job), printed, skipped

    def _pick_body(self, content: ContentSet) -> tuple[Path, bytes, list[str]]:
        """Choose a body image that actually renders.

        A receipt with a header and a footer and no verse in the middle is
        worse than a different verse, so a corrupt file costs another draw
        from the bag rather than the whole middle of the receipt.
        """
        skipped: list[str] = []
        tried: set[Path] = set()
        # The bag draws each image once per cycle, so two cycles' worth of
        # draws is enough to have seen every image even starting mid-cycle.
        for _ in range(2 * len(content.bodies)):
            if len(tried) == len(content.bodies):
                break
            body = self.bag.pick(content.bodies)
            if body in tried:
                continue
            tried.add(body)
            try:
                return body, self.cache.get(body).data, skipped
            except (OSError, ValueError) as exc:
                log.error("skipping %s: %s", body, exc)
                skipped.append(body.name)
        raise RuntimeError(
            f"none of the {len(content.bodies)} body image(s) could be rendered"
        )

    def print_receipt(self, content: ContentSet) -> PrintResult:
        """Header + a random body image + footer, then cut."""
        if not content.ready:
            raise RuntimeError(f"no body images in {content.directory}")
        self._guard()

        body, body_bytes, skipped = self._pick_body(content)
        job = bytearray(escpos.INIT + escpos.ALIGN_LEFT)
        printed: list[str] = []
        for path, data in (
            (content.header, None), (body, body_bytes), (content.footer, None)
        ):
            if path is None:
                continue
            try:
                job += data if data is not None else self.cache.get(path).data
                printed.append(path.name)
            except (OSError, ValueError) as exc:
                log.error("skipping %s: %s", path, exc)
                skipped.append(path.name)
        job += self.trailer()
        job = bytes(job)

        started = time.monotonic()
        for _ in range(self.config.printing.copies):
            self.transport.write(job)
        elapsed = time.monotonic() - started

        log.info("printed %s in %.2fs (%d bytes)", ", ".join(printed), elapsed, len(job))
        return PrintResult(
            body=body,
            sections=tuple(printed),
            bytes_sent=len(job) * self.config.printing.copies,
            seconds=elapsed,
            skipped=tuple(skipped),
        )

    def print_images(self, images: list[Path]) -> PrintResult:
        """Print an explicit list of images (used by ``joybox print --image``)."""
        self._guard()
        job, printed, skipped = self.compose(images)
        started = time.monotonic()
        self.transport.write(job)
        return PrintResult(
            sections=tuple(printed),
            bytes_sent=len(job),
            seconds=time.monotonic() - started,
            skipped=tuple(skipped),
        )

    # ---------------------------------------------------------------- text

    def print_lines(self, lines: list[str], title: str | None = None) -> None:
        """Print plain text with the printer's built-in font.

        Deliberately independent of image rendering: this is what prints the
        diagnostics slip when something about the images is what went wrong.
        """
        job = bytearray(escpos.INIT + escpos.ALIGN_LEFT + escpos.FONT_NORMAL)
        if title:
            job += escpos.ALIGN_CENTER + escpos.BOLD_ON
            job += escpos.text(title.upper() + "\n")
            job += escpos.BOLD_OFF + escpos.ALIGN_LEFT
            job += escpos.text("-" * 42 + "\n")
        for line in lines:
            job += escpos.text(line.rstrip() + "\n")
        job += self.trailer()
        self.transport.write(bytes(job))

    def feed(self, lines: int = 4) -> None:
        self.transport.write(escpos.feed(lines))

    def cut(self) -> None:
        self.transport.write(self.trailer())
