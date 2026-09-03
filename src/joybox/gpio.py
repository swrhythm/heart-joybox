"""GPIO access, with a stand-in for machines that are not a Raspberry Pi.

The CLI has to run on a laptop (``joybox render``, ``joybox doctor``) and the
tests have to run in CI, so gpiozero is imported lazily and its absence is a
degraded mode rather than a crash.
"""

from __future__ import annotations

import logging

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
