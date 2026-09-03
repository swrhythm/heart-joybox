"""The light has to tell the truth from across a room."""

from pathlib import Path

from joybox import led
from joybox.app import Joybox, render_options
from joybox.config import from_mapping
from joybox.content import ContentSet
from joybox.escpos import PrinterStatus


def joybox(content_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("JOYBOX_CACHE_DIR", str(tmp_path / "cache"))
    settings = from_mapping({
        "content": {"dir": str(content_dir)},
        "led": {"enabled": False},
        "printer": {"device": str(tmp_path / "absent"), "fallback_devices": []},
    })
    return Joybox(settings)


def test_ready_when_there_are_images_and_the_printer_is_fine(content_dir, tmp_path, monkeypatch):
    box = joybox(content_dir, tmp_path, monkeypatch)
    box.status = PrinterStatus(online=True, source="test")
    assert box.current_pattern() is led.READY


def test_no_paper_outranks_everything_else(content_dir, tmp_path, monkeypatch):
    box = joybox(content_dir, tmp_path, monkeypatch)
    box.status = PrinterStatus(paper_out=True, source="test")
    assert box.current_pattern() is led.NO_PAPER


def test_a_disconnected_printer_shows_the_printer_code(content_dir, tmp_path, monkeypatch):
    box = joybox(content_dir, tmp_path, monkeypatch)
    box.refresh_status()                              # the device really is absent
    assert box.status.online is False
    assert box.current_pattern() is led.PRINTER_ERROR


def test_an_empty_card_shows_the_content_code(content_dir, tmp_path, monkeypatch):
    box = joybox(content_dir, tmp_path, monkeypatch)
    box.status = PrinterStatus(online=True, source="test")
    box.content = ContentSet(directory=content_dir)
    assert box.current_pattern() is led.NO_CONTENT


def test_printing_wins_while_a_job_is_in_flight(content_dir, tmp_path, monkeypatch):
    box = joybox(content_dir, tmp_path, monkeypatch)
    box.status = PrinterStatus(paper_out=True, source="test")
    box.busy = True
    assert box.current_pattern() is led.PRINTING


def test_an_unknown_status_is_not_treated_as_broken(content_dir, tmp_path, monkeypatch):
    box = joybox(content_dir, tmp_path, monkeypatch)
    box.status = PrinterStatus()                      # printer never answered
    assert box.current_pattern() is led.READY


def test_images_added_to_the_card_are_noticed_without_a_restart(content_dir, tmp_path, monkeypatch):
    from conftest import write_image

    box = joybox(content_dir, tmp_path, monkeypatch)
    assert len(box.content.bodies) == 3
    write_image(content_dir / "body" / "4.png")
    box.refresh_content()
    assert len(box.content.bodies) == 4


def test_render_options_follow_the_config():
    options = render_options(from_mapping({"print": {"width_dots": 512, "dither": True}}))
    assert options.width_dots == 512 and options.dither
