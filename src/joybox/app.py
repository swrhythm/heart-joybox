"""The long-running service: watch the button, print, keep the light honest."""

from __future__ import annotations

import logging
import os
import queue
import signal
import threading
import time
from pathlib import Path

from . import gpio, health, led
from .button import ButtonWatcher, PressPolicy
from .cache import RenderCache
from .config import Config
from .content import ContentSet, scan
from .escpos import PrinterStatus
from .printer import PrintBlocked, ReceiptPrinter
from .raster import RenderOptions
from .transport import CharDeviceTransport, TransportError

log = logging.getLogger(__name__)

PRESS, HOLD = "press", "hold"

CONTENT_POLL_SECONDS = 5.0
STATUS_POLL_SECONDS = 15.0
TICK_SECONDS = 0.25


def render_options(config: Config) -> RenderOptions:
    return RenderOptions(
        width_dots=config.printing.width_dots,
        dither=config.printing.dither,
        threshold=config.printing.threshold,
        max_height=config.printing.max_image_height,
        band_height=config.printing.band_height,
    )


class Joybox:
    def __init__(self, config: Config):
        self.config = config
        self.events: queue.Queue[str] = queue.Queue(maxsize=8)
        self.stopping = threading.Event()

        self.cache = RenderCache(render_options(config))
        self.transport = CharDeviceTransport(
            config.printer.device,
            config.printer.fallback_devices,
            chunk_bytes=config.printer.write_chunk_bytes,
            pause_seconds=config.printer.write_pause_seconds,
            write_timeout_seconds=config.printer.write_timeout_seconds,
            status_timeout_seconds=config.printer.status_timeout_seconds,
        )
        self.printer = ReceiptPrinter(config, self.transport, self.cache)
        self.policy = PressPolicy(
            config.button.cooldown_seconds, config.button.max_prints_per_hour
        )
        self.led = led.StatusLed(config.led.gpio if config.led.enabled else None,
                                 config.led.active_high)
        self.button: ButtonWatcher | None = None
        self.watchdog = health.Watchdog()

        self.content: ContentSet = self._scan()
        self.status: PrinterStatus = PrinterStatus()
        self.busy = False
        self.last_error = ""
        self.prints = 0
        self.last_print_at = 0.0
        self._fingerprint = self.content.fingerprint()
        self._warming: threading.Thread | None = None

    # ---------------------------------------------------------------- setup

    def _scan(self) -> ContentSet:
        settings = self.config.content
        return scan(settings.dir, settings.header, settings.footer, settings.body_dir)

    def _warm_cache(self) -> None:
        if self._warming is not None and self._warming.is_alive():
            return
        images = self.content.all_images()
        if not images:
            return

        def work() -> None:
            started = time.monotonic()
            ok, failed = self.cache.warm(images)
            self.cache.prune(images)
            log.info(
                "pre-rendered %d image(s)%s in %.1fs",
                ok, f", {failed} failed" if failed else "", time.monotonic() - started,
            )

        self._warming = threading.Thread(target=work, name="joybox-warm", daemon=True)
        self._warming.start()

    def _emit(self, event: str) -> None:
        try:
            self.events.put_nowait(event)
        except queue.Full:
            log.debug("dropping %s: still working through the last one", event)

    # ---------------------------------------------------------------- events

    def handle_press(self) -> None:
        if not self.content.ready:
            log.warning("button pressed but there is nothing to print")
            return
        decision = self.policy.check()
        if not decision.allowed:
            log.info("press ignored: %s (%.0fs to go)", decision.reason, decision.retry_after)
            return

        self.busy = True
        self.led.show(led.PRINTING)
        try:
            self.content = self._scan()
            result = self.printer.print_receipt(self.content)
            self.policy.record()
            self.prints += 1
            self.last_print_at = time.time()
            self.last_error = ""
            self.status = PrinterStatus(online=True, source="print")
            if result.skipped:
                log.warning("skipped unreadable image(s): %s", ", ".join(result.skipped))
        except PrintBlocked as exc:
            self.status = exc.status
            self.last_error = str(exc)
            log.warning("%s", exc)
        except (TransportError, RuntimeError, OSError) as exc:
            self.last_error = str(exc)
            self.status = PrinterStatus(online=False, source="write-failed")
            log.error("print failed: %s", exc)
        finally:
            self.busy = False
            self._drain()

    def handle_hold(self) -> None:
        """Print the diagnostics slip - the station's answer to 'what is wrong'."""
        log.info("button held; printing diagnostics")
        self.busy = True
        self.led.show(led.PRINTING)
        try:
            self.content = self._scan()
            extra = {
                "prints": str(self.prints),
                "last": time.strftime("%H:%M:%S", time.localtime(self.last_print_at))
                if self.last_print_at else "none this session",
                "service": health.service_state(),
            }
            if self.last_error:
                extra["error"] = self.last_error
            lines = health.diagnostics_lines(
                self.config, self.content, self.printer.status().describe(), extra
            )
            self.printer.print_lines(lines, title="heart joybox status")
        except (TransportError, RuntimeError, OSError) as exc:
            self.last_error = str(exc)
            log.error("could not print diagnostics: %s", exc)
        finally:
            self.busy = False
            self._drain()

    def _drain(self) -> None:
        """Throw away presses that piled up while we were printing."""
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return

    # ------------------------------------------------------------ periodics

    def refresh_content(self) -> None:
        """Pick up images added to the card without needing a restart."""
        self.content = self._scan()
        fingerprint = self.content.fingerprint()
        if fingerprint != self._fingerprint:
            self._fingerprint = fingerprint
            log.info("content changed: %d body image(s)", len(self.content.bodies))
            self._warm_cache()

    def refresh_status(self) -> None:
        self.status = self.printer.status()

    def current_pattern(self) -> led.Pattern:
        if self.busy:
            return led.PRINTING
        # A button we never managed to claim is, to whoever is standing there,
        # exactly a jammed button: pressing it does nothing.  Say so on the light
        # rather than showing a steady "ready" to an empty room.
        if self.button is not None and (self.button.stuck or not self.button.active):
            return led.PRINTER_ERROR
        if self.status.paper_out or self.status.cover_open:
            return led.NO_PAPER
        if self.status.online is False:
            return led.PRINTER_ERROR
        if not self.content.ready:
            return led.NO_CONTENT
        return led.READY

    def status_text(self) -> str:
        if self.busy:
            return "printing"
        # systemctl reads this through sd_notify, and the doctor quotes it back.
        # On a station with no status light it is the only place the fault shows.
        if self.button is not None and not self.button.active:
            return "button not watched - run 'joybox doctor'"
        pattern = self.current_pattern()
        if pattern is led.READY:
            return f"ready - {len(self.content.bodies)} images, {self.prints} printed this session"
        return pattern.meaning

    # ----------------------------------------------------------------- run

    def run(self) -> int:
        self.led.show(led.STARTING)
        for name in (signal.SIGTERM, signal.SIGINT):
            signal.signal(name, lambda *_: self.stopping.set())

        self.button = ButtonWatcher(
            self.config.button,
            on_press=lambda: self._emit(PRESS),
            on_hold=lambda: self._emit(HOLD),
        )
        if not self.button.active:
            log.error("no GPIO button: presses will not be seen (run 'joybox doctor')")
        driver = gpio.factory_name()
        if driver:
            log.info("gpio pins driven by %s", driver)

        self._warm_cache()
        self.refresh_status()
        for problem in self.config.problems:
            log.warning("config: %s", problem)
        for problem in self.content.problems:
            log.info("content: %s", problem)

        health.notify("READY=1\nSTATUS=" + self.status_text())
        log.info("joybox ready: %s", self.printer.transport.describe())

        last_content = last_status = last_reported = 0.0
        reported = ""
        while not self.stopping.is_set():
            try:
                event = self.events.get(timeout=TICK_SECONDS)
            except queue.Empty:
                event = None

            if event == PRESS:
                self.handle_press()
            elif event == HOLD:
                self.handle_hold()

            now = time.monotonic()
            if self.button is not None:
                self.button.poll()
            if now - last_content >= CONTENT_POLL_SECONDS:
                last_content = now
                self.refresh_content()
            if not self.busy and now - last_status >= STATUS_POLL_SECONDS:
                last_status = now
                self.refresh_status()

            self.led.show(self.current_pattern())
            self.watchdog.ping()

            text = self.status_text()
            if text != reported and now - last_reported > 1.0:
                reported, last_reported = text, now
                health.notify("STATUS=" + text)

        return self.shutdown()

    def shutdown(self) -> int:
        log.info("shutting down")
        health.notify("STOPPING=1")
        if self.button is not None:
            self.button.close()
        self.led.close()
        self.transport.close()
        return 0


def configure_logging(level: str) -> None:
    """Plain lines under systemd (journald stamps them); timestamps otherwise."""
    under_systemd = bool(os.environ.get("JOURNAL_STREAM") or os.environ.get("INVOCATION_ID"))
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s" if under_systemd
        else "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def run(config: Config) -> int:
    configure_logging(config.system.log_level)
    return Joybox(config).run()
