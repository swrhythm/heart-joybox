"""The commands the setup and troubleshooting guides tell you to run."""

import io
import sys
from pathlib import Path

import pytest

from joybox.__main__ import main
from conftest import write_image

from joybox import gpio, health


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Keep the CLI away from a real /boot and a real printer."""
    monkeypatch.setenv("JOYBOX_CONTENT_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("JOYBOX_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr("joybox.paths.ETC_CONFIG", tmp_path / "no-etc.toml")


def test_render_writes_a_preview_you_can_look_at(tmp_path, capsys):
    image = write_image(tmp_path / "art.png", width=300, height=100)
    preview = tmp_path / "preview.png"
    assert main(["render", str(image), "--preview", str(preview)]) == 0
    assert preview.exists()
    assert "576x192 dots" in capsys.readouterr().out


def test_render_reports_how_much_paper_an_image_will_use(tmp_path, capsys):
    image = write_image(tmp_path / "art.png", width=576, height=1015)
    main(["render", str(image)])
    assert "127 mm of paper" in capsys.readouterr().out


def test_render_of_a_broken_file_fails_cleanly(tmp_path, capsys):
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a png")
    assert main(["render", str(broken)]) == 1
    assert "cannot render" in capsys.readouterr().err


def test_list_shows_the_images_that_would_print(content_dir, capsys):
    assert main(["--content", str(content_dir), "list"]) == 0
    out = capsys.readouterr().out
    assert "header: header.png" in out and "body:   3 image(s)" in out


def test_list_fails_when_the_card_is_empty(tmp_path, capsys):
    assert main(["--content", str(tmp_path / "empty"), "list"]) == 1


def test_print_dry_run_needs_no_printer(content_dir, capsys):
    assert main(["--content", str(content_dir), "print", "--dry-run"]) == 0
    assert "printed:" in capsys.readouterr().out


def test_print_writes_a_job_file_you_can_inspect(content_dir, tmp_path):
    job = tmp_path / "job.bin"
    assert main(["--content", str(content_dir), "print", "--output", str(job)]) == 0
    data = job.read_bytes()
    assert data.startswith(b"\x1b@") and data.endswith(b"\x1dVB\x00")


def test_print_says_what_is_missing_when_there_is_nothing_to_print(tmp_path, capsys):
    assert main(["--content", str(tmp_path / "empty"), "print", "--dry-run"]) == 1
    assert "nothing to print" in capsys.readouterr().err


def test_test_page_renders_at_the_configured_width(tmp_path, capsys):
    job = tmp_path / "selftest.bin"
    assert main(["test", "--output", str(job)]) == 0
    assert "576x" in capsys.readouterr().out
    assert job.read_bytes().startswith(b"\x1b@")


def test_diagnostics_prints_without_touching_the_image_pipeline(tmp_path):
    job = tmp_path / "slip.bin"
    assert main(["diagnostics", "--output", str(job)]) == 0
    assert b"\x1dv0\x00" not in job.read_bytes()


def test_doctor_exits_nonzero_when_something_is_wrong(capsys):
    assert main(["doctor"]) == 2
    assert "check(s) failed" in capsys.readouterr().out


def test_status_reports_a_missing_printer(capsys):
    assert main(["status"]) == 1
    assert "offline" in capsys.readouterr().out


def test_an_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        main(["frobnicate"])


def test_a_filename_the_card_mangled_does_not_break_the_listing(tmp_path, monkeypatch):
    """A FAT32 card can hand us bytes that are not UTF-8; a listing must survive.

    The stream is forced to errors="strict" because that is what an SSH login
    under a regional UTF-8 locale gets, and it is the case that used to raise.
    """
    content = tmp_path / "content"
    write_image(content / "body" / "1.png")
    (content / "body" / "caf\udce9.txt").write_bytes(b"junk")

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8", errors="strict", write_through=True)
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", stream)

    assert main(["--content", str(content), "list"]) == 0
    stream.flush()
    assert "caf" in raw.getvalue().decode("utf-8")


def test_doctor_calls_a_crash_looping_service_a_failure(monkeypatch, capsys):
    """The station this was written for read [warn] on its twenty-first restart."""
    monkeypatch.setattr(health, "service_facts", lambda unit=health.UNIT: {
        "LoadState": "loaded", "ActiveState": "activating", "SubState": "auto-restart",
        "Result": "exit-code", "NRestarts": "21", "ActiveEnterTimestampMonotonic": "0"})
    assert main(["doctor"]) == 2
    assert "[FAIL] service" in capsys.readouterr().out


def test_doctor_fails_when_gpiozero_cannot_watch_a_press(monkeypatch, tmp_path, capsys):
    chip = tmp_path / "gpiochip0"
    chip.touch()
    monkeypatch.setattr(health, "GPIOCHIP", chip)
    monkeypatch.setattr(gpio, "available", lambda: True)
    monkeypatch.setattr(gpio, "pin_factory", lambda: gpio.PinFactory(
        "NativeFactory", ("Falling back from lgpio: [Errno 2] ... '.lgd-nfy-3'",)))
    assert main(["doctor"]) == 2
    out = capsys.readouterr().out
    assert "[FAIL] gpio driver" in out and "lgd-nfy" in out


def test_doctor_says_nothing_alarming_where_there_is_no_gpio(monkeypatch, capsys):
    monkeypatch.setattr(health, "GPIOCHIP", Path("/definitely/not/here"))
    main(["doctor"])
    assert "[warn] gpio driver" in capsys.readouterr().out
