"""Renders are cached so a button press is a byte blit, not a computation."""

from pathlib import Path

from joybox.cache import RenderCache
from joybox.raster import RenderOptions
from conftest import write_image


def cache(tmp_path: Path, **options) -> RenderCache:
    return RenderCache(RenderOptions(width_dots=64, **options), tmp_path / "cache")


def test_a_second_process_reuses_what_the_first_rendered(tmp_path):
    image = write_image(tmp_path / "a.png", width=64)
    first = cache(tmp_path).get(image)
    second = cache(tmp_path)                          # cold instance, warm disk
    assert second._read_disk(list(second.directory.glob("*.bin"))[0].stem) is not None
    assert second.get(image).data == first.data


def test_editing_an_image_invalidates_its_cache_entry(tmp_path):
    image = write_image(tmp_path / "a.png", width=64, height=20)
    store = cache(tmp_path)
    before = store.get(image)
    write_image(tmp_path / "a.png", width=64, height=50)
    assert store.get(image).height != before.height


def test_changing_the_print_width_invalidates_the_cache(tmp_path):
    image = write_image(tmp_path / "a.png", width=64)
    narrow = cache(tmp_path).get(image)
    wide = RenderCache(RenderOptions(width_dots=576), tmp_path / "cache").get(image)
    assert narrow.width == 64 and wide.width == 576


def test_warm_reports_what_it_could_not_render(tmp_path):
    good = write_image(tmp_path / "good.png", width=64)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert cache(tmp_path).warm([good, bad]) == (1, 1)


def test_prune_removes_renders_of_deleted_images(tmp_path):
    keep = write_image(tmp_path / "keep.png", width=64)
    drop = write_image(tmp_path / "drop.png", width=64)
    store = cache(tmp_path)
    store.warm([keep, drop])
    assert store.prune([keep]) == 1
    assert len(list((tmp_path / "cache").glob("*.bin"))) == 1


def test_an_unwritable_cache_degrades_instead_of_failing(tmp_path, monkeypatch):
    image = write_image(tmp_path / "a.png", width=64)
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    store = RenderCache(RenderOptions(width_dots=64), tmp_path / "nope")
    assert not store.disk_enabled
    assert store.get(image).data                      # still renders, just not cached


def test_a_truncated_cache_file_is_re_rendered(tmp_path):
    image = write_image(tmp_path / "a.png", width=64)
    store = cache(tmp_path)
    store.get(image)
    blob = next((tmp_path / "cache").glob("*.bin"))
    blob.write_bytes(b"junk")
    fresh = cache(tmp_path)
    assert fresh.get(image).width == 64
