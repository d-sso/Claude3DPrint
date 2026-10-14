import pytest

import print3d_profile as pp
from print3d_profile import fit, hole, insert_hole, load_profile, nut_pocket, profile_value, screw_clearance


@pytest.fixture(autouse=True)
def fresh_profile():
    pp.activate(load_profile())
    yield
    pp.activate(None)


def test_base_values():
    assert fit("sliding") == 0.3
    assert fit("press") == 0.1
    assert hole(5.0) == 5.2
    assert screw_clearance("M3") == 3.6
    assert insert_hole("M3") == 4.0
    assert insert_hole("M2.5") == 3.6
    assert nut_pocket("M3") == (5.8, 2.6)
    assert profile_value("printer.layer_height") == 0.2


def test_values_follow_overrides():
    pp.activate(load_profile(overrides={"holes.compensation": 0.1, "fits.sliding": 0.35, "nuts.af_clearance": 0.4}))
    assert hole(5.0) == 5.1
    assert screw_clearance("M3") == 3.5
    assert fit("sliding") == 0.35
    assert nut_pocket("M3") == (5.9, 2.6)


def test_results_rounded_to_3_decimals():
    pp.activate(load_profile(overrides={"holes.compensation": 0.1234}))
    assert hole(3.0) == 3.123


@pytest.mark.parametrize(
    "call,needle",
    [
        (lambda: fit("tight"), "press"),
        (lambda: screw_clearance("M7"), "M3"),
        (lambda: insert_hole("M8"), "M3"),
        (lambda: nut_pocket("M1"), "M3"),
        (lambda: profile_value("holes.nope"), "compensation"),
        (lambda: profile_value("nope.x"), "printer"),
    ],
)
def test_unknown_options_raise_value_error_listing_choices(call, needle):
    with pytest.raises(ValueError) as err:
        call()
    assert needle in str(err.value)


def test_used_records_exactly_the_keys_read():
    fit("sliding")
    screw_clearance("M3")
    insert_hole("M2.5")
    nut_pocket("M4")
    assert pp.used() == {
        "fits.sliding": 0.3,
        "holes.compensation": 0.2,
        "inserts.M2.5": 3.6,
        "nuts.af_clearance": 0.3,
        "nuts.height_clearance": 0.2,
    }


def test_activate_resets_record():
    fit("snug")
    pp.activate(load_profile())
    assert pp.used() == {}


def test_used_returns_a_copy():
    fit("snug")
    pp.used().clear()
    assert pp.used() == {"fits.snug": 0.2}


def test_fallback_without_activation_uses_base_profile():
    pp.activate(None)
    assert fit("loose") == 0.5
    assert pp.used() == {"fits.loose": 0.5}
