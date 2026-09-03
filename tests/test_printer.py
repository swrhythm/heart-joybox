"""Job composition, and what happens when the printer is unhappy."""

import random
from pathlib import Path

import pytest

from joybox import escpos
from joybox.cache import RenderCache
from joybox.config import from_mapping
from joybox.content import ShuffleBag, scan
from joybox.escpos import PrinterStatus
from joybox.printer import PrintBlocked, ReceiptPrinter
from joybox.raster import RenderOptions
from joybox.transport import MemoryTransport
from conftest import write_image


def build(content_dir: Path, tmp_path: Path, overrides=None, status=None):
    settings = from_mapping({"content": {"dir": str(content_dir)}, **(overrides or {})})
    cache = RenderCache(RenderOptions(width_dots=settings.printing.width_dots),
                        tmp_path / "cache")
    transport = MemoryTransport(status)
    printer = ReceiptPrinter(settings, transport, cache, ShuffleBag(random.Random(0)))
    return printer, transport


def test_a_receipt_is_header_body_footer_then_a_cut(content_dir, tmp_path):
    printer, transport = build(content_dir, tmp_path)
    result = printer.print_receipt(scan(content_dir))

    assert len(result.sections) == 3
    assert result.sections[0] == "header.png" and result.sections[-1] == "footer.png"
    job = bytes(transport.buffer)
    assert job.startswith(escpos.INIT + escpos.ALIGN_LEFT)
    assert job.endswith(escpos.feed(4) + escpos.cut("partial"))
    assert job.count(b"\x1dv0\x00") >= 3            # one raster run per image


def test_the_body_image_is_the_only_part_that_changes(content_dir, tmp_path):
    printer, _ = build(content_dir, tmp_path)
    content = scan(content_dir)
    bodies = {printer.print_receipt(content).body for _ in range(6)}
    assert len(bodies) == 3                          # all three, none repeated forever


def test_a_missing_footer_just_drops_that_section(content_dir, tmp_path):
    (content_dir / "footer.png").unlink()
    printer, _ = build(content_dir, tmp_path)
    result = printer.print_receipt(scan(content_dir))
    assert "footer.png" not in result.sections
    assert len(result.sections) == 2


def test_a_corrupt_header_does_not_sink_the_receipt(content_dir, tmp_path):
    (content_dir / "header.png").write_bytes(b"this is not a png")
    printer, transport = build(content_dir, tmp_path)
    result = printer.print_receipt(scan(content_dir))

    assert result.skipped == ("header.png",)
    assert len(result.sections) == 2                 # body and footer still printed
    assert bytes(transport.buffer).endswith(escpos.cut("partial"))


def test_a_corrupt_verse_is_replaced_by_another_one(content_dir, tmp_path):
    """An empty middle is worse than a different verse."""
    for name in ("1.png", "2.png"):
        (content_dir / "body" / name).write_bytes(b"this is not a png")
    printer, _ = build(content_dir, tmp_path)

    for _ in range(5):
        result = printer.print_receipt(scan(content_dir))
        assert result.body.name == "3.png"           # the only one that renders
        assert len(result.sections) == 3             # header, verse, footer


def test_every_verse_being_corrupt_is_an_error_not_a_blank_receipt(content_dir, tmp_path):
    for image in (content_dir / "body").iterdir():
        image.write_bytes(b"this is not a png")
    printer, transport = build(content_dir, tmp_path)
    with pytest.raises(RuntimeError, match="none of the 3 body image"):
        printer.print_receipt(scan(content_dir))
    assert not transport.buffer


def test_out_of_paper_blocks_the_job_instead_of_buffering_it(content_dir, tmp_path):
    printer, transport = build(content_dir, tmp_path,
                               status=PrinterStatus(paper_out=True, source="test"))
    with pytest.raises(PrintBlocked) as raised:
        printer.print_receipt(scan(content_dir))
    assert "out of paper" in str(raised.value)
    assert not transport.buffer                      # nothing was queued at the printer


def test_an_unreadable_status_still_prints(content_dir, tmp_path):
    printer, transport = build(content_dir, tmp_path, status=PrinterStatus())
    printer.print_receipt(scan(content_dir))
    assert transport.buffer


def test_cut_none_leaves_the_paper_attached(content_dir, tmp_path):
    printer, transport = build(content_dir, tmp_path, {"print": {"cut": "none"}})
    printer.print_receipt(scan(content_dir))
    assert bytes(transport.buffer).endswith(escpos.feed(4))


def test_copies_repeat_the_whole_job(content_dir, tmp_path):
    single, transport_one = build(content_dir, tmp_path)
    single.print_receipt(scan(content_dir))
    double, transport_two = build(content_dir, tmp_path, {"print": {"copies": 2}})
    double.print_receipt(scan(content_dir))
    assert len(transport_two.buffer) == 2 * len(transport_one.buffer)


def test_nothing_to_print_is_a_clear_error(tmp_path):
    empty = tmp_path / "empty"
    (empty / "body").mkdir(parents=True)
    printer, _ = build(empty, tmp_path)
    with pytest.raises(RuntimeError, match="no body images"):
        printer.print_receipt(scan(empty))


def test_the_diagnostics_slip_does_not_go_through_the_image_pipeline(content_dir, tmp_path):
    """It has to print when broken images are the problem."""
    write_image(content_dir / "body" / "1.png")
    printer, transport = build(content_dir, tmp_path)
    printer.print_lines(["network   192.168.1.5"], title="status")
    job = bytes(transport.buffer)
    assert b"\x1dv0\x00" not in job                  # no raster commands at all
    assert b"STATUS" in job and b"192.168.1.5" in job
