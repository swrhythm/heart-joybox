"""The rules that protect an unattended paper roll."""

import pytest
from conftest import fake_gpiozero

from joybox import gpio
from joybox.button import ButtonWatcher, PressPolicy, StuckDetector
from joybox.config import ButtonConfig


@pytest.fixture
def clock():
    """A clock the test drives: set clock[0] to jump forward in time."""
    return [0.0]


def policy(clock, cooldown=5.0, per_hour=0):
    return PressPolicy(cooldown, per_hour, clock=lambda: clock[0])


def test_the_first_press_always_prints(clock):
    assert policy(clock).check().allowed


def test_mashing_the_button_only_prints_once(clock):
    rules = policy(clock)
    rules.record()
    for moment in (0.1, 1.0, 4.9):
        clock[0] = moment
        decision = rules.check()
        assert not decision.allowed and decision.reason == "cooling down"


def test_the_cooldown_expires(clock):
    rules = policy(clock)
    rules.record()
    clock[0] = 5.0
    assert rules.check().allowed


def test_the_cooldown_reports_how_long_is_left(clock):
    rules = policy(clock)
    rules.record()
    clock[0] = 2.0
    assert rules.check().retry_after == pytest.approx(3.0)


def test_an_hourly_cap_stops_a_determined_visitor(clock):
    rules = policy(clock, cooldown=0.0, per_hour=3)
    for index in range(3):
        clock[0] = index
        assert rules.check().allowed
        rules.record()
    clock[0] = 4
    decision = rules.check()
    assert not decision.allowed and "hourly limit" in decision.reason


def test_the_hourly_cap_is_a_rolling_window(clock):
    rules = policy(clock, cooldown=0.0, per_hour=2)
    rules.record()
    clock[0] = 10
    rules.record()
    clock[0] = 20
    assert not rules.check().allowed
    clock[0] = 3601                                  # the first press has aged out
    assert rules.check().allowed


def test_no_cap_by_default(clock):
    rules = policy(clock, cooldown=0.0)
    for index in range(200):
        clock[0] = index
        assert rules.check().allowed
        rules.record()


def test_a_jammed_button_locks_itself_out(clock):
    detector = StuckDetector(30.0, clock=lambda: clock[0])
    assert not detector.update(True)
    clock[0] = 29.9
    assert not detector.update(True) and not detector.stuck
    clock[0] = 30.0
    assert detector.update(True) and detector.stuck


def test_releasing_a_jammed_button_brings_it_back(clock):
    detector = StuckDetector(30.0, clock=lambda: clock[0])
    detector.update(True)
    clock[0] = 40
    detector.update(True)
    assert detector.stuck
    detector.update(False)
    assert not detector.stuck
    clock[0] = 41
    assert not detector.update(True)                 # the timer restarted


def test_becoming_stuck_is_reported_once_not_every_tick(clock):
    detector = StuckDetector(10.0, clock=lambda: clock[0])
    detector.update(True)
    clock[0] = 11
    assert detector.update(True)
    clock[0] = 12
    assert not detector.update(True) and detector.stuck


# --------------------------------------------------- claiming the real pin

class _FakeButton:
    """The parts of gpiozero.Button that ButtonWatcher touches."""

    def __init__(self, pin, pull_up=True, bounce_time=None, hold_time=1.0, hold_repeat=False):
        self.pin = pin
        self.is_pressed = False
        self.closed = False
        self.when_pressed = self.when_held = None

    def close(self):
        self.closed = True


class _Unclaimable(_FakeButton):
    """No pin factory at all - what a Pi with a broken lgpio does."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("Unable to find pin factory 'lgpio'")


class _NoEdges(_FakeButton):
    """Constructs, then refuses edge alerts - what a missing .lgd-nfy pipe does."""

    def __setattr__(self, name, value):
        if name == "when_pressed" and value is not None:
            raise OSError("[Errno 2] No such file or directory: '.lgd-nfy-3'")
        super().__setattr__(name, value)


def watcher(monkeypatch, device_class, on_press=lambda: None, on_hold=None):
    monkeypatch.setattr(gpio, "gpiozero", lambda: fake_gpiozero(Button=device_class))
    return ButtonWatcher(ButtonConfig(), on_press=on_press, on_hold=on_hold)


def test_a_button_that_cannot_be_claimed_does_not_take_the_station_down(monkeypatch):
    watch = watcher(monkeypatch, _Unclaimable)       # this used to end the process
    assert not watch.active
    watch.poll()                                     # the main loop calls both every tick
    watch.close()


def test_a_button_that_cannot_watch_edges_gives_the_pin_back(monkeypatch):
    made = []

    class _Recording(_NoEdges):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    watch = watcher(monkeypatch, _Recording)
    assert not watch.active
    assert made and made[0].closed


def test_a_claimed_button_reaches_the_press_handler(monkeypatch):
    presses = []
    watch = watcher(monkeypatch, _FakeButton, on_press=lambda: presses.append(1))
    assert watch.active
    watch._device.when_pressed()
    assert presses == [1]
