"""Unit tests for check_model: thresholds, mesh conversion and each check on planted faults."""
import numpy as np
import pytest
import trimesh
from build123d import Align, Axis, Box, BuildPart, Cylinder, Locations, Mode, fillet, offset

import check_model
from check_model import DEFAULTS, resolve_thresholds

BED = (Align.CENTER, Align.CENTER, Align.MIN)


# --- thresholds ---------------------------------------------------------------------------------

def test_defaults_match_printer():
    thresholds, sources = resolve_thresholds()
    assert thresholds == DEFAULTS
    assert thresholds["min_wall"] == 0.8 and thresholds["min_feature"] == 0.4
    assert (thresholds["bed_x"], thresholds["bed_y"], thresholds["bed_z"]) == (256.0, 256.0, 256.0)
    assert set(sources.values()) == {"default"}


def test_layers_apply_in_order_and_record_sources():
    thresholds, sources = resolve_thresholds(
        {"min_wall": 1, "samples": 500}, {"min_wall": 0.6}, {"min_wall": "1.2", "overhang_angle": "50"}
    )
    assert thresholds["min_wall"] == 1.2 and isinstance(thresholds["min_wall"], float)
    assert thresholds["samples"] == 500 and thresholds["overhang_angle"] == 50.0
    assert sources["min_wall"] == "cli" and sources["samples"] == "config" and sources["bed_x"] == "default"
    _, sources = resolve_thresholds(None, {"min_wall": 0.6})
    assert sources["min_wall"] == "model"


@pytest.mark.parametrize(
    "layers, needle",
    [
        (({"bogus": 1}, None, None), "Unknown check threshold(s) in config: bogus. Valid: bed_x"),
        ((None, {"min_wal": 1}, None), "in model: min_wal"),
        ((None, None, {"min_wall": "abc"}), "Check threshold 'min_wall': cannot parse 'abc' as float"),
        ((None, {"min_wall": True}, None), "expected float, got bool"),
        (({"min_wall": "1"}, None, None), "expected float, got str"),
        ((None, None, {"samples": "50"}), "'samples' must be at least 100"),
        ((None, None, {"samples": "1.5"}), "expected int, got float"),
        ((None, None, {"min_wall": "0"}), "'min_wall' must be greater than 0"),
        ((None, None, {"bed_margin": "-1"}), "'bed_margin' must be greater than 0"),
    ],
)
def test_bad_thresholds_raise_clear_errors(layers, needle):
    with pytest.raises(ValueError) as err:
        resolve_thresholds(*layers)
    assert needle in str(err.value)


PROFILE = {
    "name": "test-printer",
    "values": {
        "printer": {"bed_x": 180.0, "bed_y": 180.0, "bed_z": 180.0, "nozzle": 0.4},
        "checks": {"bed_margin": 3.0, "min_wall": 1.0, "min_feature": 0.5, "overhang_angle": 50.0},
    },
}


def test_profile_layer_sits_between_defaults_and_config():
    thresholds, sources = resolve_thresholds(profile=PROFILE)
    assert thresholds["bed_x"] == 180.0 and thresholds["min_wall"] == 1.0 and thresholds["overhang_angle"] == 50.0
    assert sources["bed_x"] == sources["min_wall"] == "profile"
    assert sources["samples"] == sources["size_tol"] == sources["overhang_min_area"] == "default"
    thresholds, sources = resolve_thresholds({"min_wall": 0.9}, profile=PROFILE)
    assert thresholds["min_wall"] == 0.9 and sources["min_wall"] == "config" and sources["min_feature"] == "profile"


def test_profile_supplies_only_printer_thresholds():
    assert set(check_model.PROFILE_KEYS) == {
        "bed_x", "bed_y", "bed_z", "bed_margin", "min_wall", "min_feature", "overhang_angle"
    }
    assert check_model.profile_checks(None) == {}
