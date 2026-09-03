"""Config is edited on a laptop with no validation. It must never crash-loop."""

from pathlib import Path

from joybox import config as config_module
from joybox.config import from_mapping, load


def test_defaults_match_the_hardware_we_ship_for():
    settings = from_mapping({})
    assert settings.printing.width_dots == 576      # 80mm at 203 dpi
    assert settings.printing.cut == "partial"
    assert settings.button.gpio == 17 and settings.led.gpio == 27
    assert not settings.problems


def test_a_bad_value_falls_back_and_is_reported():
    settings = from_mapping({"print": {"width_dots": "very wide"}})
    assert settings.printing.width_dots == 576
    assert any("width_dots" in problem for problem in settings.problems)


def test_an_out_of_range_value_is_clamped_back_to_the_default():
    settings = from_mapping({"print": {"threshold": 9000, "copies": 99}})
    assert settings.printing.threshold == 128
    assert settings.printing.copies == 1
    assert len(settings.problems) == 2


def test_an_unknown_choice_is_rejected():
    settings = from_mapping({"print": {"cut": "shred"}})
    assert settings.printing.cut == "partial"


def test_true_is_not_accepted_where_a_number_belongs():
    settings = from_mapping({"button": {"cooldown_seconds": True}})
    assert settings.button.cooldown_seconds == 5.0


def test_the_led_is_disabled_if_it_would_fight_the_button_for_a_pin():
    settings = from_mapping({"led": {"gpio": 17}})
    assert not settings.led.enabled
    assert any("both 17" in problem for problem in settings.problems)


def test_a_section_that_is_not_a_table_is_ignored():
    settings = from_mapping({"button": "gpio 17"})
    assert settings.button.gpio == 17
    assert any("not a table" in problem for problem in settings.problems)


def test_broken_toml_is_ignored_rather_than_fatal(tmp_path: Path, monkeypatch):
    good = tmp_path / "etc.toml"
    good.write_text('[print]\nwidth_dots = 512\n')
    broken = tmp_path / "card.toml"
    broken.write_text('[print\nwidth_dots = ')
    monkeypatch.setattr(config_module.paths, "ETC_CONFIG", good)
    monkeypatch.setattr(config_module.paths, "boot_config", lambda: broken)

    settings = load()
    assert settings.printing.width_dots == 512      # the readable file still applies
    assert any("not valid TOML" in problem for problem in settings.problems)


def test_the_card_overrides_etc(tmp_path: Path, monkeypatch):
    etc = tmp_path / "etc.toml"
    etc.write_text('[print]\nwidth_dots = 512\ncut = "full"\n')
    card = tmp_path / "card.toml"
    card.write_text('[print]\nwidth_dots = 576\n')
    monkeypatch.setattr(config_module.paths, "ETC_CONFIG", etc)
    monkeypatch.setattr(config_module.paths, "boot_config", lambda: card)

    settings = load()
    assert settings.printing.width_dots == 576      # card wins
    assert settings.printing.cut == "full"          # unset on the card, kept from etc
    assert len(settings.sources) == 2


def test_the_shipped_config_template_parses_cleanly():
    template = Path(__file__).resolve().parent.parent / "content-template" / "config.toml"
    settings = load(template)
    assert not settings.problems, settings.problems
    assert settings.printing.width_dots == 576
