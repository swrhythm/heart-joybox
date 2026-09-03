"""Watchdog, diagnostics slip, and the pre-flight check.

``joybox doctor`` is what the install script runs at the end and what you run
over SSH when something is off.  Holding the button prints the same facts on
paper, which is how the station gets diagnosed with no laptop present.
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__, gpio, paths
from .cache import RenderCache
from .config import Config
from .content import ContentSet, scan
from .raster import RenderOptions
from .transport import CharDeviceTransport

log = logging.getLogger(__name__)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


# --------------------------------------------------------------- systemd

def notify(state: str) -> bool:
    """Send a message to systemd (sd_notify), if we were started by it."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as sock:
            sock.connect(address)
            sock.sendall(state.encode("utf-8"))
        return True
    except OSError as exc:
        log.debug("sd_notify failed: %s", exc)
        return False


class Watchdog:
    """Feeds systemd's ``WatchdogSec`` so a wedged process gets restarted."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        micros = int(os.environ.get("WATCHDOG_USEC", "0") or 0)
        self.interval = micros / 2_000_000 if micros else 0.0
        self._next = self._clock() + self.interval

    @property
    def enabled(self) -> bool:
        return self.interval > 0

    def ping(self) -> bool:
        if not self.enabled or self._clock() < self._next:
            return False
        self._next = self._clock() + self.interval
        return notify("WATCHDOG=1")


# ----------------------------------------------------------- system facts

def ip_addresses() -> list[str]:
    try:
        output = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "scope", "global"],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        output = ""
    found = [
        line.split()[3].split("/")[0]
        for line in output.splitlines()
        if len(line.split()) > 3
    ]
    if not found:
        try:
            found = [socket.gethostbyname(socket.gethostname())]
        except OSError:
            found = []
    return found


def uptime() -> str:
    try:
        seconds = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError):
        return "unknown"
    hours, rest = divmod(int(seconds), 3600)
    return f"{hours}h {rest // 60}m"


def free_space(path: Path) -> str:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return "unknown"
    return f"{usage.free / 1_048_576:.0f} MB free of {usage.total / 1_048_576:.0f} MB"


def service_state(unit: str = "joybox.service") -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit], capture_output=True, text=True, timeout=5, check=False
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# --------------------------------------------------------- diagnostics slip

def diagnostics_lines(config: Config, content: ContentSet, printer_state: str,
                      extra: dict[str, str] | None = None) -> list[str]:
    """The text printed when someone holds the button down."""
    addresses = ip_addresses()
    lines = [
        f"version   {__version__}",
        f"host      {socket.gethostname()}",
        f"network   {', '.join(addresses) if addresses else 'not connected'}",
        f"uptime    {uptime()}",
        f"time      {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"printer   {printer_state}",
        f"device    {config.printer.device}",
        f"width     {config.printing.width_dots} dots",
        "",
        f"content   {content.directory}",
        f"header    {'yes' if content.header else 'MISSING'}",
        f"footer    {'yes' if content.footer else 'MISSING'}",
        f"body      {len(content.bodies)} image(s)",
    ]
    for path in content.bodies[:12]:
        lines.append(f"          - {path.name}")
    if len(content.bodies) > 12:
        lines.append(f"          ... and {len(content.bodies) - 12} more")

    for key, value in (extra or {}).items():
        lines += ["", f"{key:9s} {value}"]

    if config.problems:
        lines += ["", "config problems:"]
        lines += [f"  ! {problem}" for problem in config.problems]
    if content.problems:
        lines += ["", "content notes:"]
        lines += [f"  ! {problem}" for problem in content.problems]

    lines += ["", f"card slot {free_space(content.directory)}"]
    return lines


# ---------------------------------------------------------------- doctor

@dataclass
class Check:
    name: str
    status: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _check_images(config: Config, content: ContentSet) -> Check:
    if not content.ready:
        return Check("images render", FAIL, "no body images to render")
    options = RenderOptions(
        width_dots=config.printing.width_dots,
        dither=config.printing.dither,
        threshold=config.printing.threshold,
        max_height=config.printing.max_image_height,
        band_height=config.printing.band_height,
    )
    cache = RenderCache(options)
    ok, failed = cache.warm(content.all_images())
    if failed:
        return Check("images render", FAIL, f"{failed} image(s) could not be rendered; see the log")
    return Check("images render", PASS, f"{ok} image(s) ready to print")


def run_checks(config: Config) -> list[Check]:
    """Everything that has to be true for a button press to make a receipt."""
    checks: list[Check] = []

    checks.append(Check("joybox", PASS, f"version {__version__}, python {sys.version_info[0]}.{sys.version_info[1]}"))

    try:
        import PIL  # noqa: F401

        checks.append(Check("pillow", PASS, f"version {PIL.__version__}"))
    except ImportError:
        checks.append(Check("pillow", FAIL, "not installed - run: sudo apt install python3-pil"))

    if gpio.available():
        checks.append(Check("gpiozero", PASS, "installed"))
    else:
        checks.append(Check("gpiozero", FAIL,
                            f"{gpio.unavailable_reason()} - run: sudo apt install python3-gpiozero python3-lgpio"))

    chip = Path("/dev/gpiochip0")
    if not chip.exists():
        checks.append(Check("gpio access", WARN, "no /dev/gpiochip0 - not a Raspberry Pi?"))
    elif os.access(chip, os.R_OK | os.W_OK):
        checks.append(Check("gpio access", PASS, f"{chip} is writable by {_whoami()}"))
    else:
        checks.append(Check("gpio access", FAIL,
                            f"{_whoami()} cannot use {chip} - run: sudo adduser {_whoami()} gpio"))

    sources = ", ".join(str(p) for p in config.sources) or "built-in defaults only"
    if config.problems:
        checks.append(Check("config", WARN, f"{sources}; {len(config.problems)} problem(s): "
                                            + "; ".join(config.problems)))
    else:
        checks.append(Check("config", PASS, sources))

    content = scan(
        config.content.dir, config.content.header, config.content.footer, config.content.body_dir
    )
    if not config.content.dir.is_dir():
        checks.append(Check("content folder", FAIL, f"{config.content.dir} does not exist"))
    elif not os.access(config.content.dir, os.R_OK | os.X_OK):
        checks.append(Check("content folder", FAIL,
                            f"{config.content.dir} is not readable by {_whoami()}"))
    else:
        checks.append(Check("content folder", PASS,
                            f"{config.content.dir} ({free_space(config.content.dir)})"))

    if content.ready:
        missing = [n for n, p in (("header", content.header), ("footer", content.footer)) if not p]
        detail = f"{len(content.bodies)} body image(s)"
        if missing:
            checks.append(Check("images", WARN, f"{detail}; no {' or '.join(missing)} image"))
        else:
            checks.append(Check("images", PASS, f"{detail}, header and footer present"))
        checks.append(_check_images(config, content))
    else:
        checks.append(Check("images", FAIL,
                            f"no printable images in {config.content.dir / config.content.body_dir}"))

    cache_dir = paths.cache_dir()
    cache = RenderCache(RenderOptions(), cache_dir)
    checks.append(
        Check("render cache", PASS if cache.disk_enabled else WARN,
              str(cache_dir) if cache.disk_enabled
              else f"{cache_dir} not writable; images re-render on every boot")
    )

    transport = CharDeviceTransport(
        config.printer.device,
        config.printer.fallback_devices,
        status_timeout_seconds=config.printer.status_timeout_seconds,
    )
    device = transport.find_device()
    if device is None:
        checks.append(Check("printer device", FAIL,
                            "not found - is the printer powered on and plugged into the Pi's "
                            "middle micro-USB port?"))
    else:
        try:
            transport.open(force=True)
            status = transport.status()
            level = WARN if status.blocked else PASS
            checks.append(Check("printer device", level, f"{transport.describe()}: {status.describe()}"))
        except Exception as exc:
            checks.append(Check("printer device", FAIL, str(exc)))
        finally:
            transport.close()

    state = service_state()
    checks.append(Check("service", PASS if state == "active" else WARN, f"joybox.service is {state}"))
    return checks


def _whoami() -> str:
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:  # pragma: no cover
        return str(os.geteuid())
