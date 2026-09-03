"""The commands the setup and troubleshooting guides tell you to run."""

from pathlib import Path

import pytest

from joybox.__main__ import main
from conftest import write_image


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
