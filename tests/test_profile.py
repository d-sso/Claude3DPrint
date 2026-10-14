import pytest

import print3d_profile as pp
from print3d_profile import ProfileError, load_profile

BASE = """title = "Test printer"
[printer]
bed_x = 200.0
bed_y = 200.0
bed_z = 180.0
nozzle = 0.4
layer_height = 0.2
[holes]
compensation = 0.2
[fits]
press = 0.1
snug = 0.2
sliding = 0.3
loose = 0.5
[inserts]
M2 = 3.2
"M2.5" = 3.6
M3 = 4.0
M4 = 5.6
M5 = 6.4
[nuts]
af_clearance = 0.3
height_clearance = 0.2
[checks]
bed_margin = 5.0
min_wall = 0.8
min_feature = 0.4
overhang_angle = 45.0
"""


@pytest.fixture
def profiles(tmp_path):
    def _write(name="test-printer", text=BASE):
        (tmp_path / f"{name}.toml").write_text(text, encoding="utf-8")
        return tmp_path

    return _write


def test_base_profile_loads_with_spec_values():
    prof = load_profile()
    assert prof["name"] == "centauri-carbon-pla-plus"
    assert prof["title"].startswith("Elegoo Centauri Carbon")
    assert prof["file"].endswith("profiles/centauri-carbon-pla-plus.toml")
    v = prof["values"]
    assert (v["printer"]["bed_x"], v["printer"]["bed_y"], v["printer"]["bed_z"]) == (256.0, 256.0, 256.0)
    assert v["holes"]["compensation"] == 0.2
    assert v["fits"] == {"press": 0.1, "snug": 0.2, "sliding": 0.3, "loose": 0.5}
    assert v["inserts"]["M2.5"] == 3.6 and v["inserts"]["M3"] == 4.0
    assert v["checks"]["min_wall"] == 0.8
    assert prof["overrides"] == {}


def test_available_profiles_lists_base():
    assert "centauri-carbon-pla-plus" in pp.available_profiles()


def test_unknown_profile_lists_available(profiles):
    d = profiles()
    with pytest.raises(ProfileError) as err:
        load_profile("nope", profiles_dir=d)
    assert "nope" in str(err.value) and "test-printer" in str(err.value)


def test_bad_profile_name_rejected(profiles):
    with pytest.raises(ProfileError):
        load_profile("Bad_Name", profiles_dir=profiles())


def test_missing_section_names_file(profiles):
    d = profiles(text=BASE.replace("[nuts]\naf_clearance = 0.3\nheight_clearance = 0.2\n", ""))
    with pytest.raises(ProfileError) as err:
        load_profile("test-printer", profiles_dir=d)
    assert "[nuts]" in str(err.value) and "test-printer.toml" in str(err.value)


def test_missing_key_named(profiles):
    d = profiles(text=BASE.replace("snug = 0.2\n", ""))
    with pytest.raises(ProfileError) as err:
        load_profile("test-printer", profiles_dir=d)
    assert "fits.snug" in str(err.value)


@pytest.mark.parametrize(
    "old,new,needle",
    [
        ("min_wall = 0.8", "min_wall = 0.0", "checks.min_wall"),
        ("compensation = 0.2", "compensation = -0.1", "holes.compensation"),
        ("press = 0.1", "press = true", "fits.press"),
        ("nozzle = 0.4", 'nozzle = "0.4"', "printer.nozzle"),
        ("press = 0.1", "press = -1.5", "fits.press"),
        ("loose = 0.5", "loose = 2.5", "fits.loose"),
    ],
)
def test_out_of_range_or_wrong_type_rejected(profiles, old, new, needle):
    d = profiles(text=BASE.replace(old, new))
    with pytest.raises(ProfileError) as err:
        load_profile("test-printer", profiles_dir=d)
    assert needle in str(err.value)


def test_zero_allowed_for_compensation_and_fits(profiles):
    d = profiles(text=BASE.replace("compensation = 0.2", "compensation = 0.0").replace("press = 0.1", "press = 0.0"))
    prof = load_profile("test-printer", profiles_dir=d)
    assert prof["values"]["holes"]["compensation"] == 0.0 and prof["values"]["fits"]["press"] == 0.0


def test_overrides_applied_and_recorded():
    prof = load_profile(overrides={"holes.compensation": 0.15, "fits.snug": "0.25"})
    assert prof["values"]["holes"]["compensation"] == 0.15
    assert prof["values"]["fits"]["snug"] == 0.25
    assert prof["overrides"] == {"holes.compensation": 0.15, "fits.snug": 0.25}


def test_int_override_is_accepted_as_float():
    prof = load_profile(overrides={"fits.press": 0})
    assert prof["values"]["fits"]["press"] == 0.0 and isinstance(prof["values"]["fits"]["press"], float)


def test_dotted_key_with_dot_in_key():
    prof = load_profile(overrides={"inserts.M2.5": 3.5})
    assert prof["values"]["inserts"]["M2.5"] == 3.5


@pytest.mark.parametrize(
    "overrides,needle",
    [
        ({"holes.nope": 1.0}, "holes.nope"),
        ({"nope.x": 1.0}, "nope.x"),
        ({"compensation": 1.0}, "section.key"),
        ({"fits.snug": True}, "fits.snug"),
        ({"fits.snug": "abc"}, "fits.snug"),
        ({"checks.min_wall": 1e999}, "checks.min_wall"),
        ({"checks.min_wall": -1.0}, "checks.min_wall"),
    ],
)
def test_bad_overrides_rejected(overrides, needle):
    with pytest.raises(ProfileError) as err:
        load_profile(overrides=overrides)
    assert needle in str(err.value)


def test_split_key_first_dot_only():
    assert pp.split_key("inserts.M2.5") == ("inserts", "M2.5")


def test_negative_fits_allowed(profiles):
    d = profiles(text=BASE.replace("press = 0.1", "press = -0.2").replace("snug = 0.2", "snug = -1.0"))
    prof = load_profile("test-printer", profiles_dir=d)
    assert prof["values"]["fits"]["press"] == -0.2 and prof["values"]["fits"]["snug"] == -1.0


def test_fit_limits_are_inclusive_and_named():
    assert load_profile(overrides={"fits.loose": 2.0})["values"]["fits"]["loose"] == 2.0
    with pytest.raises(ProfileError) as err:
        load_profile(overrides={"fits.press": "-1.01"})
    assert "fits.press" in str(err.value) and "between -1 and 2" in str(err.value)


def test_negative_fit_through_helpers():
    pp.activate(load_profile(overrides={"fits.press": -0.2}))
    try:
        assert pp.fit("press") == -0.2
        assert pp.hole(10 + pp.fit("press")) == 10.0  # base compensation 0.2
    finally:
        pp.activate(None)
