"""Layered configuration.

Order of precedence (later wins):

    built-in defaults  ->  /etc/heart-joybox/config.toml  ->  <boot>/heart-joybox/config.toml

The boot-partition file is the one an operator edits on a laptop, with no way to
validate it before putting the card back in the Pi.  A typo there must never
turn into a crash loop, so every value is range-checked and a bad value falls
back to the default with a complaint recorded in ``Config.problems`` (surfaced
by ``joybox doctor``) instead of raising.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

CUT_MODES = ("partial", "full", "none")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


@dataclass(frozen=True)
class PrinterConfig:
    device: str = "/dev/joybox-printer"
    fallback_devices: tuple[str, ...] = ("/dev/usb/lp0", "/dev/usb/lp1", "/dev/lp0")
    write_chunk_bytes: int = 4096
    write_pause_seconds: float = 0.0
    write_timeout_seconds: float = 30.0
    status_check: bool = True
    status_timeout_seconds: float = 0.4


@dataclass(frozen=True)
class PrintConfig:
    width_dots: int = 576
    dither: bool = False
    threshold: int = 128
    max_image_height: int = 3000
    band_height: int = 128
    feed_lines_before_cut: int = 4
    cut: str = "partial"
    copies: int = 1


@dataclass(frozen=True)
class ButtonConfig:
    gpio: int = 17
    pull_up: bool = True
    bounce_seconds: float = 0.05
    hold_seconds: float = 5.0
    cooldown_seconds: float = 5.0
    max_prints_per_hour: int = 0  # 0 = unlimited
    stuck_seconds: float = 30.0
    diagnostics_on_hold: bool = True


@dataclass(frozen=True)
class LedConfig:
    enabled: bool = True
    gpio: int = 27
    active_high: bool = True


@dataclass(frozen=True)
class ContentConfig:
    dir: Path = field(default_factory=paths.content_dir)
    header: str = "header.png"
    footer: str = "footer.png"
    body_dir: str = "body"


@dataclass(frozen=True)
class SystemConfig:
    log_level: str = "INFO"
    warm_cache_on_start: bool = True


@dataclass(frozen=True)
class Config:
    printer: PrinterConfig = field(default_factory=PrinterConfig)
    printing: PrintConfig = field(default_factory=PrintConfig)
    button: ButtonConfig = field(default_factory=ButtonConfig)
    led: LedConfig = field(default_factory=LedConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    sources: tuple[Path, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def header_path(self) -> Path:
        return self.content.dir / self.content.header

    @property
    def footer_path(self) -> Path:
        return self.content.dir / self.content.footer

    @property
    def body_path(self) -> Path:
        return self.content.dir / self.content.body_dir


class _Reader:
    """Pulls typed values out of a raw table, complaining instead of raising."""

    def __init__(self, table: dict[str, Any], section: str, problems: list[str]):
        self.table = table if isinstance(table, dict) else {}
        self.section = section
        self.problems = problems
        if not isinstance(table, dict) and table is not None:
            problems.append(f"[{section}] is not a table; ignored")

    def _complain(self, key: str, value: Any, expected: str, default: Any) -> None:
        self.problems.append(
            f"{self.section}.{key} = {value!r} is not {expected}; using {default!r}"
        )

    def integer(self, key: str, default: int, lo: int, hi: int) -> int:
        value = self.table.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int):
            self._complain(key, value, "a whole number", default)
            return default
        if not lo <= value <= hi:
            self._complain(key, value, f"between {lo} and {hi}", default)
            return default
        return value

    def number(self, key: str, default: float, lo: float, hi: float) -> float:
        value = self.table.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self._complain(key, value, "a number", default)
            return default
        if not lo <= value <= hi:
            self._complain(key, value, f"between {lo} and {hi}", default)
            return default
        return float(value)

    def boolean(self, key: str, default: bool) -> bool:
        value = self.table.get(key, default)
        if not isinstance(value, bool):
            self._complain(key, value, "true or false", default)
            return default
        return value

    def text(self, key: str, default: str, choices: tuple[str, ...] | None = None) -> str:
        value = self.table.get(key, default)
        if not isinstance(value, str):
            self._complain(key, value, "text", default)
            return default
        if choices is not None and value.lower() not in choices:
            self._complain(key, value, f"one of {', '.join(choices)}", default)
            return default
        return value.lower() if choices is not None else value

    def text_list(self, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        value = self.table.get(key, default)
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return tuple(value)
        if key in self.table:
            self._complain(key, value, "a list of paths", list(default))
        return default


def _read_file(path: Path, problems: list[str]) -> dict[str, Any] | None:
    """Parse one TOML file.  Any failure is a complaint, never an exception."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return None
    except tomllib.TOMLDecodeError as exc:
        problems.append(f"{path}: not valid TOML ({exc}); file ignored")
    except OSError as exc:
        problems.append(f"{path}: could not be read ({exc}); file ignored")
    return None


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def from_mapping(raw: dict[str, Any], problems: list[str] | None = None,
                 sources: tuple[Path, ...] = ()) -> Config:
    """Build a validated Config from an already-parsed mapping."""
    problems = problems if problems is not None else []
    defaults = Config()

    p = _Reader(raw.get("printer", {}), "printer", problems)
    printer = PrinterConfig(
        device=p.text("device", defaults.printer.device),
        fallback_devices=p.text_list("fallback_devices", defaults.printer.fallback_devices),
        write_chunk_bytes=p.integer("write_chunk_bytes", defaults.printer.write_chunk_bytes, 64, 262144),
        write_pause_seconds=p.number("write_pause_seconds", defaults.printer.write_pause_seconds, 0.0, 5.0),
        write_timeout_seconds=p.number(
            "write_timeout_seconds", defaults.printer.write_timeout_seconds, 1.0, 300.0),
        status_check=p.boolean("status_check", defaults.printer.status_check),
        status_timeout_seconds=p.number(
            "status_timeout_seconds", defaults.printer.status_timeout_seconds, 0.0, 10.0),
    )

    r = _Reader(raw.get("print", {}), "print", problems)
    printing = PrintConfig(
        width_dots=r.integer("width_dots", defaults.printing.width_dots, 8, 2048),
        dither=r.boolean("dither", defaults.printing.dither),
        threshold=r.integer("threshold", defaults.printing.threshold, 1, 254),
        max_image_height=r.integer("max_image_height", defaults.printing.max_image_height, 8, 20000),
        band_height=r.integer("band_height", defaults.printing.band_height, 1, 1024),
        feed_lines_before_cut=r.integer(
            "feed_lines_before_cut", defaults.printing.feed_lines_before_cut, 0, 20),
        cut=r.text("cut", defaults.printing.cut, CUT_MODES),
        copies=r.integer("copies", defaults.printing.copies, 1, 3),
    )

    b = _Reader(raw.get("button", {}), "button", problems)
    button = ButtonConfig(
        gpio=b.integer("gpio", defaults.button.gpio, 0, 27),
        pull_up=b.boolean("pull_up", defaults.button.pull_up),
        bounce_seconds=b.number("bounce_seconds", defaults.button.bounce_seconds, 0.0, 2.0),
        hold_seconds=b.number("hold_seconds", defaults.button.hold_seconds, 1.0, 60.0),
        cooldown_seconds=b.number("cooldown_seconds", defaults.button.cooldown_seconds, 0.0, 600.0),
        max_prints_per_hour=b.integer(
            "max_prints_per_hour", defaults.button.max_prints_per_hour, 0, 10000),
        stuck_seconds=b.number("stuck_seconds", defaults.button.stuck_seconds, 5.0, 3600.0),
        diagnostics_on_hold=b.boolean(
            "diagnostics_on_hold", defaults.button.diagnostics_on_hold),
    )

    l = _Reader(raw.get("led", {}), "led", problems)
    led = LedConfig(
        enabled=l.boolean("enabled", defaults.led.enabled),
        gpio=l.integer("gpio", defaults.led.gpio, 0, 27),
        active_high=l.boolean("active_high", defaults.led.active_high),
    )

    c = _Reader(raw.get("content", {}), "content", problems)
    content_dir = c.text("dir", "")
    content = ContentConfig(
        dir=Path(content_dir) if content_dir else defaults.content.dir,
        header=c.text("header", defaults.content.header),
        footer=c.text("footer", defaults.content.footer),
        body_dir=c.text("body_dir", defaults.content.body_dir),
    )

    s = _Reader(raw.get("system", {}), "system", problems)
    system = SystemConfig(
        log_level=s.text("log_level", defaults.system.log_level).upper(),
        warm_cache_on_start=s.boolean(
            "warm_cache_on_start", defaults.system.warm_cache_on_start),
    )
    if system.log_level not in LOG_LEVELS:
        problems.append(
            f"system.log_level = {system.log_level!r} is not one of "
            f"{', '.join(LOG_LEVELS)}; using INFO"
        )
        system = SystemConfig(log_level="INFO", warm_cache_on_start=system.warm_cache_on_start)

    if button.gpio == led.gpio and led.enabled:
        problems.append(
            f"button.gpio and led.gpio are both {button.gpio}; LED disabled to protect the pin"
        )
        led = LedConfig(enabled=False, gpio=led.gpio, active_high=led.active_high)

    return Config(
        printer=printer,
        printing=printing,
        button=button,
        led=led,
        content=content,
        system=system,
        sources=sources,
        problems=tuple(problems),
    )


def load(extra: Path | None = None) -> Config:
    """Load the layered configuration.  Never raises."""
    problems: list[str] = []
    raw: dict[str, Any] = {}
    sources: list[Path] = []

    candidates = [paths.ETC_CONFIG, paths.boot_config()]
    if extra is not None:
        candidates.append(extra)

    for path in candidates:
        parsed = _read_file(path, problems)
        if parsed is not None:
            raw = _merge(raw, parsed)
            sources.append(path)

    return from_mapping(raw, problems, tuple(sources))
