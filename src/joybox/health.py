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

UNIT = "joybox.service"
GPIOCHIP = Path("/dev/gpiochip0")


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
            capture_output=True, text=True, errors="replace", timeout=3, check=False,
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
        seconds = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
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


def service_facts(unit: str = UNIT) -> dict[str, str]:
    """The systemd properties that tell "running" from "restarting forever".

    ``systemctl is-active`` answers ``activating`` for a service that is dying
    every three seconds and ``active`` for one that is fine, which is how a
    station on its twenty-first restart passed its own pre-flight check.
    """
    wanted = ("LoadState", "ActiveState", "SubState", "Result", "NRestarts",
              "StatusText", "ActiveEnterTimestampMonotonic")
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "--property=" + ",".join(wanted)],
            capture_output=True, text=True, errors="replace", timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    facts = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            facts[key] = value
    return facts


def _whole(value: str | None) -> int:
    try:
        return int(value or 0)
    except ValueError:
        return 0


def _running_for(facts: dict[str, str]) -> float | None:
    """Seconds since the unit last became active, or None if it never has.

    systemd's monotonic stamps and time.monotonic() are both CLOCK_MONOTONIC,
    so they subtract directly.
    """
    micros = _whole(facts.get("ActiveEnterTimestampMonotonic"))
    if micros <= 0:
        return None
    return max(0.0, time.monotonic() - micros / 1_000_000)


def service_state(unit: str = UNIT) -> str:
    """One line for the diagnostics slip."""
    facts = service_facts(unit)
    state = facts.get("ActiveState") or "unknown"
    restarts = _whole(facts.get("NRestarts"))
    return f"{state} ({restarts} restarts)" if restarts else state


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


def _check_pin_factory() -> Check:
    """Can gpiozero actually drive these pins?

    "gpio access" only proves the device node is readable and writable, which is
    why it read [ok] through twenty restarts: nothing ever asked gpiozero to
    choose a driver, and choosing is the step that was failing.
    """
    if not GPIOCHIP.exists():
        return Check("gpio driver", WARN, "not checked: this machine has no GPIO")
    if not gpio.available():
        return Check("gpio driver", WARN, "not checked: gpiozero is missing (see above)")
    try:
        factory = gpio.pin_factory()
    except NotImplementedError as exc:
        # Not a fault on this Pi, so do not cry wolf about one.
        return Check("gpio driver", WARN, f"not checked: {exc}")
    except Exception as exc:  # BadPinFactory, ImportError, OSError - same meaning
        return Check("gpio driver", FAIL,
                     f"gpiozero cannot drive the pins ({exc}) - run: "
                     "sudo apt install python3-gpiozero python3-lgpio")
    if not factory.watches_edges:
        why = f"; {factory.fallbacks[0]}" if factory.fallbacks else ""
        return Check("gpio driver", FAIL,
                     f"gpiozero fell back to {factory.name}, which cannot watch the button on "
                     f"this kernel - the light works and no press is ever seen{why} - run: "
                     "sudo apt install python3-lgpio")
    return Check("gpio driver", PASS, f"gpiozero drives the pins with {factory.name}")


def _check_service(unit: str = UNIT) -> Check:
    facts = service_facts(unit)
    if not facts:
        return Check("service", WARN, "no systemd here, so nothing is watching the button")
    if facts.get("LoadState") == "not-found":
        return Check("service", WARN, f"{unit} is not installed - run: sudo ./scripts/install.sh")

    journal = "run: journalctl -u joybox -b | tail -40"
    state = facts.get("ActiveState", "unknown")
    restarts = _whole(facts.get("NRestarts"))
    running_for = _running_for(facts)

    if state == "failed" or facts.get("SubState") == "auto-restart":
        return Check("service", FAIL,
                     f"{unit} is dying and being restarted ({restarts} so far, last result: "
                     f"{facts.get('Result', 'unknown')}) - {journal}")
    if state != "active":
        # Stopping the service by hand is a documented step, so this is not a
        # failure - but it does mean a press will do nothing right now.
        return Check("service", WARN, f"{unit} is {state} - run: sudo systemctl start joybox")
    # With RestartSec=3 a single sample lands on auto-restart only about three
    # times in four, so catch the loop by its restart count as well.
    if restarts >= 2 and running_for is not None and running_for < 60:
        return Check("service", FAIL,
                     f"{unit} has restarted {restarts} times, the last one {running_for:.0f}s "
                     f"ago - {journal}")
    status = facts.get("StatusText", "")
    detail = f"{unit} is active" + (f": {status}" if status else "")
    if restarts:
        return Check("service", WARN, f"{detail}, after {restarts} restart(s) - {journal}")
    return Check("service", PASS, detail)


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

    chip = GPIOCHIP
    if not chip.exists():
        checks.append(Check("gpio access", WARN, "no /dev/gpiochip0 - not a Raspberry Pi?"))
    elif os.access(chip, os.R_OK | os.W_OK):
        checks.append(Check("gpio access", PASS, f"{chip} is writable by {_whoami()}"))
    else:
        checks.append(Check("gpio access", FAIL,
                            f"{_whoami()} cannot use {chip} - run: sudo adduser {_whoami()} gpio"))
    checks.append(_check_pin_factory())

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

    checks.append(_check_service())
    return checks


def _whoami() -> str:
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except Exception:  # pragma: no cover
        return str(os.geteuid())
