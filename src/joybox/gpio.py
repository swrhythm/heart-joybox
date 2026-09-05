"""GPIO access, with a stand-in for machines that are not a Raspberry Pi.

The CLI has to run on a laptop (``joybox render``, ``joybox doctor``) and the
tests have to run in CI, so gpiozero is imported lazily and its absence is a
degraded mode rather than a crash.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_gpiozero = None
_probed = False
_reason = ""


def gpiozero():
    """Return the gpiozero module, or None if this machine has no GPIO."""
    global _gpiozero, _probed, _reason
    if not _probed:
        _probed = True
        try:
            import gpiozero  # type: ignore

            _gpiozero = gpiozero
        except Exception as exc:  # pragma: no cover - depends on the machine
            _reason = str(exc)
            log.info("gpiozero unavailable (%s); GPIO features disabled", exc)
    return _gpiozero


def unavailable_reason() -> str:
    gpiozero()
    return _reason or "gpiozero is not installed"


def available() -> bool:
    return gpiozero() is not None


# ------------------------------------------------------- which driver, though

@dataclass(frozen=True)
class PinFactory:
    """Which driver gpiozero settled on, and what it gave up on to get there."""

    name: str
    fallbacks: tuple[str, ...] = ()

    @property
    def watches_edges(self) -> bool:
        """Can this driver see a button press?

        The native driver reaches outputs by mapping /dev/gpiomem, so a light
        works, but it watches edges through /sys/class/gpio, where the pin
        numbering is offset by the chip base on current kernels.  A station on
        it lights up, looks ready, and never sees a press.
        """
        return self.name != "NativeFactory"


@contextlib.contextmanager
def _notify_dir():
    """Import lgpio somewhere it is allowed to create its alert pipe.

    lgpio opens one FIFO per process, at import.  Its C half creates it at
    $LG_WD/.lgd-nfy<n>, falling back to the working directory; its Python half
    opens .lgd-nfy<n> relative to the working directory and never checks whether
    the C half succeeded.  Started somewhere this account cannot write - "/",
    which is where systemd puts a service with no WorkingDirectory= - the import
    raises, and gpiozero quietly drops to a driver that cannot watch a button.

    That is worth reporting about the service, but the doctor must not trip over
    it itself: run from somebody else's directory it would otherwise cry wolf
    about a perfectly healthy station.  So it probes from a directory of its own
    and leaves no stray .lgd-nfy0 behind.
    """
    previous = None
    with contextlib.suppress(OSError):
        previous = os.getcwd()
    was = os.environ.get("LG_WD")
    with tempfile.TemporaryDirectory(prefix="joybox-lgpio-",
                                     ignore_cleanup_errors=True) as workdir:
        os.environ["LG_WD"] = workdir
        with contextlib.suppress(OSError):
            os.chdir(workdir)
        try:
            yield Path(workdir)
        finally:
            if was is None:
                os.environ.pop("LG_WD", None)
            else:
                os.environ["LG_WD"] = was
            if previous is not None:
                with contextlib.suppress(OSError):
                    os.chdir(previous)


def pin_factory() -> PinFactory:
    """Make gpiozero choose its driver now, and report which one it chose.

    Checking that /dev/gpiochip0 is writable only proves the device node is
    there.  It is choosing a driver that fails on a broken lgpio, and gpiozero
    chooses silently - four warnings nobody reads, then a fallback.  Asking for
    the name is the only way to notice from outside.

    ``ensure_pin_factory()`` opens the chip but requests no lines, so this is
    safe to run while the service is up and holding the button, which is how the
    troubleshooting guide tells you to run the doctor.
    """
    module = gpiozero()
    if module is None:
        raise RuntimeError(unavailable_reason())
    ensure = getattr(module.Device, "ensure_pin_factory", None)
    if ensure is None:  # pragma: no cover - gpiozero older than 1.6
        raise NotImplementedError("this gpiozero cannot say which driver it picked")
    with _notify_dir(), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ensure()
        factory = module.Device.pin_factory
    return PinFactory(type(factory).__name__,
                      tuple(str(entry.message) for entry in caught))


def factory_name() -> str:
    """The driver gpiozero is already using, or "" if it has not chosen yet.

    Unlike :func:`pin_factory` this only reads what is there, so it is safe on
    the service's own start-up path where a chdir would be rude.
    """
    if _gpiozero is None:
        return ""
    factory = getattr(_gpiozero.Device, "pin_factory", None)
    return type(factory).__name__ if factory is not None else ""
