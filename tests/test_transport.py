"""Reaching the printer, and failing usefully when we cannot."""

from pathlib import Path

import pytest

from joybox.transport import (CharDeviceTransport, FileTransport, MemoryTransport,
                              TransportError)


def device(tmp_path: Path) -> Path:
    path = tmp_path / "lp0"
    path.write_bytes(b"")
    return path


def test_bytes_reach_the_device(tmp_path):
    path = device(tmp_path)
    transport = CharDeviceTransport(path)
    transport.write(b"\x1b@hello")
    transport.close()
    assert path.read_bytes() == b"\x1b@hello"


def test_a_large_job_is_written_in_full(tmp_path):
    path = device(tmp_path)
    transport = CharDeviceTransport(path, chunk_bytes=64)
    payload = bytes(range(256)) * 400                 # 100 KB, many chunks
    transport.write(payload)
    transport.close()
    assert path.read_bytes() == payload


def test_a_missing_printer_says_so_plainly(tmp_path):
    transport = CharDeviceTransport(tmp_path / "absent", [tmp_path / "also-absent"])
    with pytest.raises(TransportError, match="no printer device found"):
        transport.open(force=True)
    assert transport.describe() == "not connected"


def test_the_first_working_fallback_is_used(tmp_path):
    path = device(tmp_path)
    transport = CharDeviceTransport(tmp_path / "absent", [tmp_path / "nope", path])
    transport.open(force=True)
    assert transport.device_path == path
    transport.close()


def test_repeated_failures_back_off_instead_of_hammering(tmp_path):
    transport = CharDeviceTransport(tmp_path / "absent")
    with pytest.raises(TransportError, match="no printer device"):
        transport.open(force=True)
    with pytest.raises(TransportError, match="waiting before retrying"):
        transport.open()                              # unforced: honours the backoff
    with pytest.raises(TransportError, match="no printer device"):
        transport.open(force=True)                    # a button press still tries


def test_a_dropped_link_is_retried_once(tmp_path, monkeypatch):
    path = device(tmp_path)
    transport = CharDeviceTransport(path)
    attempts = []
    original = transport._write_once

    def flaky(data):
        attempts.append(data)
        if len(attempts) == 1:
            raise TransportError("printer was power-cycled")
        original(data)

    monkeypatch.setattr(transport, "_write_once", flaky)
    transport.write(b"second time lucky")
    transport.close()
    assert len(attempts) == 2
    assert path.read_bytes() == b"second time lucky"


def test_a_link_that_stays_down_raises(tmp_path, monkeypatch):
    transport = CharDeviceTransport(device(tmp_path))
    monkeypatch.setattr(transport, "_write_once",
                        lambda data: (_ for _ in ()).throw(TransportError("gone")))
    with pytest.raises(TransportError, match="gone"):
        transport.write(b"x")


def test_status_of_a_disconnected_printer_is_offline_not_an_exception(tmp_path):
    transport = CharDeviceTransport(tmp_path / "absent", [])
    status = transport.status()
    assert status.online is False and status.blocked


def test_memory_transport_collects_the_job():
    transport = MemoryTransport()
    transport.write(b"ab")
    transport.write(b"cd")
    assert bytes(transport.buffer) == b"abcd" and transport.writes == [b"ab", b"cd"]


def test_file_transport_writes_a_job_for_inspection(tmp_path):
    target = tmp_path / "job.bin"
    transport = FileTransport(target)
    transport.write(b"\x1dV\x42\x00")
    transport.close()
    assert target.read_bytes() == b"\x1dV\x42\x00"
