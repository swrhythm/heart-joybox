"""A small ESC/POS command builder.

Only the handful of commands this kiosk needs.  Writing them out beats pulling
in a printing library plus libusb: fewer packages to install on a Pi Zero, and
nothing between us and the bytes when something misbehaves at an event.

Command references are Epson ESC/POS, which the Iware X-Series XS-80 implements.
"""

from __future__ import annotations

from dataclasses import dataclass

ESC = b"\x1b"
GS = b"\x1d"
DLE = b"\x10"
EOT = b"\x04"

INIT = ESC + b"@"                 # ESC @   reset to power-on defaults
ALIGN_LEFT = ESC + b"a\x00"       # ESC a 0
ALIGN_CENTER = ESC + b"a\x01"     # ESC a 1
FONT_NORMAL = ESC + b"!\x00"      # ESC ! 0  built-in font A
BOLD_ON = ESC + b"E\x01"          # ESC E 1
BOLD_OFF = ESC + b"E\x00"         # ESC E 0

# DLE EOT n - real-time status.  n=1 printer, 2 offline cause, 3 error, 4 paper.
STATUS_PRINTER = DLE + EOT + b"\x01"
STATUS_OFFLINE = DLE + EOT + b"\x02"
STATUS_ERROR = DLE + EOT + b"\x03"
STATUS_PAPER = DLE + EOT + b"\x04"

# ioctl(fd, LPGETSTATUS) on a usblp character device, from <linux/lp.h>.
LPGETSTATUS = 0x060B
LP_PERRORP = 0x08   # low when the printer reports an error
LP_PSELECD = 0x10   # high when the printer is selected / online
LP_POUTPA = 0x20    # high when paper is out


def text(value: str) -> bytes:
    """Encode text for the printer's built-in font.

    Used only for the diagnostics slip, which has to print even when image
    rendering is the thing that is broken.
    """
    return value.encode("cp437", "replace")


def feed(lines: int) -> bytes:
    """ESC d n - feed n lines of paper."""
    return ESC + b"d" + bytes([max(0, min(255, lines))])


def cut(mode: str = "partial", feed_dots: int = 0) -> bytes:
    """GS V m n - cut the paper.

    ``GS V 66 n`` (partial) leaves a small tab holding the receipt on; it is
    what the XS-80's auto-cutter does and what nearly every 80mm cutter
    supports.  ``GS V 65 n`` severs the paper completely.
    """
    if mode == "none":
        return b""
    function = 65 if mode == "full" else 66
    return GS + b"V" + bytes([function, max(0, min(255, feed_dots))])


def raster_band(stride_bytes: int, rows: int, payload: bytes) -> bytes:
    """GS v 0 m xL xH yL yH d1..dk - one band of a raster bit image."""
    return (
        GS
        + b"v0\x00"
        + stride_bytes.to_bytes(2, "little")
        + rows.to_bytes(2, "little")
        + payload
    )


@dataclass(frozen=True)
class PrinterStatus:
    """What we could learn about the printer.  ``None`` means "unknown"."""

    online: bool | None = None
    paper_out: bool | None = None
    paper_low: bool | None = None
    cover_open: bool | None = None
    error: bool | None = None
    source: str = "none"
    raw: int | None = None

    @property
    def blocked(self) -> bool:
        """True only when we positively know printing cannot succeed.

        Unknown is deliberately not blocked: a printer whose status line we
        cannot read must never become a printer that refuses to print.
        """
        return bool(self.paper_out or self.cover_open or self.online is False)

    def describe(self) -> str:
        if self.paper_out:
            return "out of paper"
        if self.cover_open:
            return "cover open"
        if self.online is False:
            return "offline"
        if self.error:
            return "reporting an error"
        if self.paper_low:
            return "ready (paper low)"
        if self.online:
            return "ready"
        return "ready (status unavailable)"


def decode_port_status(value: int) -> PrinterStatus:
    """Decode the USB printer-class port status byte from LPGETSTATUS."""
    return PrinterStatus(
        online=bool(value & LP_PSELECD),
        paper_out=bool(value & LP_POUTPA),
        error=not bool(value & LP_PERRORP),
        source="usb-port",
        raw=value,
    )


def decode_paper_status(value: int) -> PrinterStatus:
    """Decode the DLE EOT 4 paper-roll-sensor byte.

    Bits 2 and 3 both set means paper near-end; bits 5 and 6 both set means the
    roll has run out.
    """
    return PrinterStatus(
        paper_low=bool(value & 0x0C),
        paper_out=bool(value & 0x60),
        source="paper-sensor",
        raw=value,
    )


def decode_offline_status(value: int) -> PrinterStatus:
    """Decode the DLE EOT 2 offline-cause byte."""
    return PrinterStatus(
        cover_open=bool(value & 0x04),
        error=bool(value & 0x40),
        source="offline-cause",
        raw=value,
    )


def merge(*statuses: PrinterStatus) -> PrinterStatus:
    """Combine status reads, letting any definite answer win over unknown."""
    result: dict[str, object] = {}
    sources: list[str] = []
    raw: int | None = None
    for status in statuses:
        if status.source == "none":
            continue
        sources.append(status.source)
        raw = status.raw if raw is None else raw
        for name in ("online", "paper_out", "paper_low", "cover_open", "error"):
            value = getattr(status, name)
            if value is None:
                continue
            # A definite "something is wrong" outranks a definite "fine".
            if result.get(name) is None or value:
                result[name] = value
    if not sources:
        return PrinterStatus()
    return PrinterStatus(source="+".join(sources), raw=raw, **result)  # type: ignore[arg-type]
