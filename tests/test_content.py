"""Finding images on a card that has been in a Mac, and picking one."""

import random
from pathlib import Path

import pytest

from joybox.content import ShuffleBag, is_junk, natural_key, scan
from conftest import write_image


def test_body_images_sort_the_way_a_person_numbers_them(content_dir: Path):
    write_image(content_dir / "body" / "10.png")
    assert [p.name for p in scan(content_dir).bodies] == \
        ["1.png", "2.png", "3.png", "10.png"]


def test_sidecar_files_from_a_mac_or_windows_are_ignored(content_dir: Path):
    for junk in ("._1.png", ".DS_Store", "Thumbs.db", "notes.txt"):
        (content_dir / "body" / junk).write_bytes(b"junk")
    names = [p.name for p in scan(content_dir).bodies]
    assert names == ["1.png", "2.png", "3.png"]


def test_empty_files_are_skipped_rather_than_printed(content_dir: Path):
    (content_dir / "body" / "4.png").write_bytes(b"")
    result = scan(content_dir)
    assert [p.name for p in result.bodies] == ["1.png", "2.png", "3.png"]
    assert any("empty" in problem for problem in result.problems)


def test_header_and_footer_are_optional(content_dir: Path):
    (content_dir / "footer.png").unlink()
    result = scan(content_dir)
    assert result.header is not None and result.footer is None
    assert result.ready
    assert any("no footer image" in problem for problem in result.problems)


def test_a_missing_content_folder_is_reported_not_raised(tmp_path: Path):
    result = scan(tmp_path / "absent")
    assert not result.ready
    assert result.problems and "does not exist" in result.problems[0]


def test_no_body_images_means_not_ready(content_dir: Path):
    for image in (content_dir / "body").iterdir():
        image.unlink()
    assert not scan(content_dir).ready


def test_fingerprint_notices_an_edited_image(content_dir: Path):
    before = scan(content_dir).fingerprint()
    write_image(content_dir / "body" / "2.png", height=90)
    assert scan(content_dir).fingerprint() != before


@pytest.mark.parametrize("name", ["._x.png", ".DS_Store", "Thumbs.db", ".hidden.png"])
def test_is_junk(name):
    assert is_junk(Path(name))


def test_natural_key_orders_numbers_numerically():
    assert sorted(["10.png", "2.png", "1.png"], key=natural_key) == \
        ["1.png", "2.png", "10.png"]


def test_every_image_prints_once_before_any_repeats():
    items = [Path(f"{n}.png") for n in range(5)]
    bag = ShuffleBag(random.Random(0))
    first_round = [bag.pick(items) for _ in range(5)]
    assert sorted(first_round) == sorted(items)


def test_a_new_round_never_opens_with_the_image_that_just_printed():
    items = [Path(f"{n}.png") for n in range(4)]
    # Enough rounds that a seed which happens to behave would be caught.
    for seed in range(50):
        bag = ShuffleBag(random.Random(seed))
        picks = [bag.pick(items) for _ in range(24)]
        assert all(a != b for a, b in zip(picks, picks[1:])), f"repeat with seed {seed}"


def test_adding_an_image_reshuffles_instead_of_going_stale():
    items = [Path("1.png"), Path("2.png")]
    bag = ShuffleBag(random.Random(1))
    bag.pick(items)
    grown = items + [Path("3.png")]
    picked = {bag.pick(grown) for _ in range(6)}
    assert Path("3.png") in picked


def test_picking_from_nothing_is_an_error_not_a_crash_later():
    with pytest.raises(ValueError):
        ShuffleBag().pick([])
