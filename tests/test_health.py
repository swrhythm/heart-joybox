"""The pre-flight check, and the probe it uses to answer honestly.

A station on its twenty-first restart used to pass `joybox doctor`, so these
cover the two checks that now tell the truth about it.
"""

import os
import time
from pathlib import Path

import pytest
from conftest import fake_gpiozero

from joybox import gpio, health


# ------------------------------------------------------------ the pin probe

class _StubFactory:
    pass


def stub_device(monkeypatch, on_ensure=None):
    seen = {}

    class Device:
        pin_factory = _StubFactory()

        @staticmethod
        def ensure_pin_factory():
            seen["cwd"] = os.getcwd()
            seen["lg_wd"] = os.environ.get("LG_WD")
            if on_ensure is not None:
                on_ensure()

    monkeypatch.setattr(gpio, "gpiozero", lambda: fake_gpiozero(Device=Device))
    return seen


def test_the_probe_imports_lgpio_somewhere_it_is_allowed_to_write(monkeypatch):
    seen = stub_device(monkeypatch)
    factory = gpio.pin_factory()
    assert factory.name == "_StubFactory"
    # The bug, inverted: the two halves of lgpio must agree on one directory.
    assert Path(seen["cwd"]).resolve() == Path(seen["lg_wd"]).resolve()


def test_the_probe_leaves_the_working_directory_where_it_found_it(monkeypatch):
    before, was = os.getcwd(), os.environ.get("LG_WD")
    stub_device(monkeypatch)
    gpio.pin_factory()
    assert os.getcwd() == before and os.environ.get("LG_WD") == was


def test_the_probe_tidies_up_even_when_gpiozero_gives_up(monkeypatch):
    before, was = os.getcwd(), os.environ.get("LG_WD")
    stub_device(monkeypatch, on_ensure=lambda: (_ for _ in ()).throw(RuntimeError("BadPinFactory")))
    with pytest.raises(RuntimeError):
        gpio.pin_factory()
    assert os.getcwd() == before and os.environ.get("LG_WD") == was


def test_the_native_driver_is_reported_as_unusable_not_as_working():
    assert not gpio.PinFactory("NativeFactory").watches_edges
    assert gpio.PinFactory("LGPIOFactory").watches_edges


# ------------------------------------------------------- the service check

def facts(**overrides):
    base = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running",
            "Result": "success", "NRestarts": "0",
            "ActiveEnterTimestampMonotonic": str(int(time.monotonic() * 1_000_000))}
    base.update(overrides)
    return base


def check(monkeypatch, mapping):
    monkeypatch.setattr(health, "service_facts", lambda unit=health.UNIT: mapping)
    return health._check_service()


def test_a_service_restarting_every_three_seconds_is_a_failure(monkeypatch):
    result = check(monkeypatch, facts(ActiveState="activating", SubState="auto-restart",
                                      Result="exit-code", NRestarts="21"))
    assert result.status == health.FAIL and "21" in result.detail


def test_a_loop_is_caught_by_its_restart_count_even_between_restarts(monkeypatch):
    # systemd shows "active" for the second the process lives, so SubState alone
    # misses the loop roughly one sample in four.
    assert check(monkeypatch, facts(NRestarts="5")).status == health.FAIL


def test_a_failed_service_is_a_failure(monkeypatch):
    assert check(monkeypatch, facts(ActiveState="failed", Result="exit-code")).status == health.FAIL


def test_a_healthy_service_passes_and_quotes_what_it_says_about_itself(monkeypatch):
    result = check(monkeypatch, facts(StatusText="ready - 4 images, 0 printed this session"))
    assert result.status == health.PASS and "4 images" in result.detail


def test_a_service_stopped_by_hand_is_not_called_broken(monkeypatch):
    # Stopping it to print by hand is a documented step in TROUBLESHOOTING.md.
    assert check(monkeypatch, facts(ActiveState="inactive", SubState="dead")).status == health.WARN


def test_no_systemd_at_all_invents_no_failure(monkeypatch):
    assert check(monkeypatch, {}).status == health.WARN


def test_a_unit_that_was_never_installed_says_so(monkeypatch):
    result = check(monkeypatch, facts(LoadState="not-found"))
    assert result.status == health.WARN and "install.sh" in result.detail


def test_one_restart_is_a_warning_not_a_loop(monkeypatch):
    assert check(monkeypatch, facts(NRestarts="1")).status == health.WARN


# ------------------------------------------------- is it safe to unplug

ORDINARY_MOUNTS = "/dev/mmcblk0p2 / ext4 rw,noatime 0 0\nproc /proc proc rw 0 0\n"
OVERLAY_MOUNTS = "overlay / overlay ro,lowerdir=/ro,upperdir=/rw 0 0\n"
DOCKER_MOUNTS = ORDINARY_MOUNTS + "overlay /var/lib/docker/overlay2/ab/merged overlay rw 0 0\n"
SHUTDOWN_DEVICE = 'I: Bus=0019 Vendor=0001\nN: Name="gpio-shutdown"\nH: Handlers=kbd event0\n'


def power_off(monkeypatch, tmp_path, mounts="", devices=""):
    for name, text in (("MOUNTS", mounts), ("INPUT_DEVICES", devices)):
        path = tmp_path / name.lower()
        path.write_text(text)
        monkeypatch.setattr(health, name, path)
    return health._check_safe_power_off()


def test_a_read_only_root_is_safe_to_unplug(monkeypatch, tmp_path):
    result = power_off(monkeypatch, tmp_path, mounts=OVERLAY_MOUNTS)
    assert result.status == health.PASS and "read-only" in result.detail


def test_a_shutdown_button_counts_as_protection(monkeypatch, tmp_path):
    result = power_off(monkeypatch, tmp_path, mounts=ORDINARY_MOUNTS, devices=SHUTDOWN_DEVICE)
    assert result.status == health.PASS and "shutdown button" in result.detail


def test_a_station_with_neither_guard_is_warned_about(monkeypatch, tmp_path):
    result = power_off(monkeypatch, tmp_path, mounts=ORDINARY_MOUNTS)
    assert result.status == health.WARN
    assert "raspi-config" in result.detail and "WIRING.md" in result.detail


def test_somebody_elses_overlay_mount_does_not_count(monkeypatch, tmp_path):
    # docker and snap both leave overlay mounts on an otherwise ordinary Pi.
    # Reading one of those as "safe to unplug" is the single mistake this check
    # exists to prevent, so match the type on "/" and nowhere else.
    assert power_off(monkeypatch, tmp_path, mounts=DOCKER_MOUNTS).status == health.WARN


def test_a_missing_proc_file_is_not_mistaken_for_protection(monkeypatch, tmp_path):
    monkeypatch.setattr(health, "MOUNTS", tmp_path / "gone")
    monkeypatch.setattr(health, "INPUT_DEVICES", tmp_path / "also-gone")
    assert health._check_safe_power_off().status == health.WARN
