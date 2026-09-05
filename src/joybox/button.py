"""The button: when a press counts, and when it does not.

Nobody is standing guard, so the rules here exist to protect the paper roll:
a short cooldown stops mashing, an optional hourly cap stops a determined
child, and a stuck or shorted button locks itself out instead of printing
until the roll runs out.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from . import gpio
from .config import ButtonConfig

log = logging.getLogger(__name__)

Clock = Callable[[], float]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    retry_after: float = 0.0


class PressPolicy:
    """Decides whether a press should turn into a receipt."""

    def __init__(self, cooldown_seconds: float, max_prints_per_hour: int = 0,
                 clock: Clock = time.monotonic):
        self.cooldown_seconds = cooldown_seconds
        self.max_prints_per_hour = max_prints_per_hour
        self._clock = clock
        self._last: float | None = None
        self._recent: deque[float] = deque()

    def check(self) -> Decision:
        now = self._clock()
        if self._last is not None:
            waited = now - self._last
            if waited < self.cooldown_seconds:
                return Decision(False, "cooling down", self.cooldown_seconds - waited)
        if self.max_prints_per_hour:
            self._expire(now)
            if len(self._recent) >= self.max_prints_per_hour:
                return Decision(
                    False,
                    f"hourly limit of {self.max_prints_per_hour} reached",
                    3600 - (now - self._recent[0]),
                )
        return Decision(True)

    def record(self) -> None:
        now = self._clock()
        self._last = now
        self._recent.append(now)
        self._expire(now)

    def _expire(self, now: float) -> None:
        while self._recent and now - self._recent[0] >= 3600:
            self._recent.popleft()

    @property
    def cooling_down(self) -> bool:
        return not self.check().allowed and self._last is not None


class StuckDetector:
    """Locks out a button that never comes back up."""

    def __init__(self, limit_seconds: float, clock: Clock = time.monotonic):
        self.limit_seconds = limit_seconds
        self._clock = clock
        self._since: float | None = None
        self.stuck = False

    def update(self, pressed: bool) -> bool:
        """Feed the current button state.  True on the transition into stuck."""
        if not pressed:
            if self.stuck:
                log.info("button released; accepting presses again")
            self._since = None
            self.stuck = False
            return False
        now = self._clock()
        if self._since is None:
            self._since = now
        if not self.stuck and now - self._since >= self.limit_seconds:
            self.stuck = True
            log.error(
                "button has been held for %.0fs; ignoring it until it is released",
                now - self._since,
            )
            return True
        return False


class ButtonWatcher:
    """Wraps the physical button and turns it into two callbacks."""

    def __init__(self, config: ButtonConfig, on_press: Callable[[], None],
                 on_hold: Callable[[], None] | None = None):
        self.config = config
        self.on_press = on_press
        self.on_hold = on_hold
        self.stuck_detector = StuckDetector(config.stuck_seconds)
        self._device = None

        module = gpio.gpiozero()
        if module is None:
            log.warning("button on GPIO %s not watched: %s", config.gpio, gpio.unavailable_reason())
            return
        try:
            self._device = module.Button(
                config.gpio,
                pull_up=config.pull_up,
                bounce_time=config.bounce_seconds or None,
                hold_time=config.hold_seconds,
                hold_repeat=False,
            )
            # Attaching the handlers is inside the try too: on some pin factories
            # that is the call that actually asks the kernel for edge alerts, so
            # it fails for the same reasons the constructor does.
            self._device.when_pressed = self._pressed
            self._device.when_held = self._held
        except Exception as exc:  # pragma: no cover - depends on hardware
            # A button we cannot claim is a station that prints nothing, but a
            # station that keeps running still answers 'joybox doctor' and can
            # still be printed from by hand.  Crashing here just hid the reason
            # behind a restart loop.
            log.warning("could not watch GPIO %s for the button: %s", config.gpio, exc)
            self.close()  # release the pin if the constructor got that far
            return
        log.info("watching button on GPIO %s", config.gpio)

    @property
    def active(self) -> bool:
        return self._device is not None

    @property
    def stuck(self) -> bool:
        return self.stuck_detector.stuck

    def _pressed(self) -> None:
        if self.stuck_detector.stuck:
            return
        try:
            self.on_press()
        except Exception:  # pragma: no cover - a callback must not kill the thread
            log.exception("button press handler failed")

    def _held(self) -> None:
        if self.stuck_detector.stuck or self.on_hold is None:
            return
        if not self.config.diagnostics_on_hold:
            return
        try:
            self.on_hold()
        except Exception:  # pragma: no cover
            log.exception("button hold handler failed")

    def poll(self) -> None:
        """Called from the main loop so a jammed button is noticed."""
        if self._device is None:
            return
        self.stuck_detector.update(bool(self._device.is_pressed))

    def close(self) -> None:
        if self._device is not None:
            try:
                self._device.close()
            except Exception:  # pragma: no cover
                pass
            self._device = None
