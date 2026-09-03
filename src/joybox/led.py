"""The status light in the button.

Whoever is standing at the station has no terminal, so the light is the whole
user interface for "is this thing working".  The same code table appears in the
README, the troubleshooting guide, and the card left at the station.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from . import gpio

log = logging.getLogger(__name__)

Step = tuple[bool, float]  # (lit, seconds)


@dataclass(frozen=True)
class Pattern:
    name: str
    steps: tuple[Step, ...]
    meaning: str


def _blinks(count: int, gap: float = 1.2) -> tuple[Step, ...]:
    return tuple([(True, 0.15), (False, 0.15)] * count) + ((False, gap),)


OFF = Pattern("off", ((False, 1.0),), "powered down")
STARTING = Pattern("starting", ((True, 0.1), (False, 0.1)), "starting up")
READY = Pattern("ready", ((True, 1.0),), "ready - press the button")
PRINTING = Pattern("printing", ((True, 0.06), (False, 0.06)), "printing")
PRINTER_ERROR = Pattern("printer-error", _blinks(2),
                        "printer offline, or the button is jammed")
NO_PAPER = Pattern("no-paper", _blinks(3), "out of paper, or the paper cover is open")
NO_CONTENT = Pattern("no-content", ((True, 1.0), (False, 1.0)),
                     "no images found on the SD card")

# Deliberately few, and none of them needs counting past three.
ALL_PATTERNS = (STARTING, READY, PRINTING, NO_CONTENT, PRINTER_ERROR, NO_PAPER)


class StatusLed:
    """Plays a blink pattern on a background thread until told otherwise."""

    def __init__(self, pin: int | None = None, active_high: bool = True):
        self._device = None
        self._pattern = OFF
        self._changed = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = False

        if pin is None:
            return
        module = gpio.gpiozero()
        if module is None:
            log.info("LED on GPIO %s not driven: %s", pin, gpio.unavailable_reason())
            return
        try:
            self._device = module.LED(pin, active_high=active_high, initial_value=False)
        except Exception as exc:  # pragma: no cover - depends on hardware
            log.warning("could not claim GPIO %s for the LED: %s", pin, exc)
            return

        self._thread = threading.Thread(target=self._run, name="joybox-led", daemon=True)
        self._thread.start()

    @property
    def active(self) -> bool:
        return self._device is not None

    @property
    def pattern(self) -> Pattern:
        return self._pattern

    def show(self, pattern: Pattern) -> None:
        if pattern is self._pattern:
            return
        self._pattern = pattern
        self._changed.set()

    def _apply(self, lit: bool) -> None:
        self.state = lit
        if self._device is None:
            return
        try:
            self._device.on() if lit else self._device.off()
        except Exception as exc:  # pragma: no cover - depends on hardware
            log.debug("LED write failed: %s", exc)

    def _run(self) -> None:
        while not self._stop.is_set():
            pattern = self._pattern
            self._changed.clear()
            for lit, seconds in pattern.steps:
                self._apply(lit)
                if self._changed.wait(seconds) or self._stop.is_set():
                    break

    def close(self) -> None:
        self._stop.set()
        self._changed.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._apply(False)
        if self._device is not None:
            try:
                self._device.close()
            except Exception:  # pragma: no cover - depends on hardware
                pass
            self._device = None
