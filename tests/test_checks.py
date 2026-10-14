"""Unit tests for check_model: thresholds, mesh conversion and each check on planted faults."""
import numpy as np
import pytest
import trimesh
from build123d import Align, Axis, Box, BuildPart, Cylinder, Locations, Mode, fillet, offset

import check_model
from check_model import DEFAULTS, check_part, mesh_from_shape, resolve_thresholds, worst_status

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


def box(x=40, y=30, z=15):
    with BuildPart() as part:
        Box(x, y, z, align=BED)
    return part.part


def results(report):
    return {r["check"]: r for r in report["results"]}


def run_checks(shape, expected=None, **overrides):
    thresholds, _ = resolve_thresholds(cli_checks={k: str(v) for k, v in overrides.items()})
    return check_part(mesh_from_shape(shape), thresholds, expected)


# --- mesh conversion and sealed -----------------------------------------------------------------

def test_mesh_from_box_is_sealed():
    mesh = mesh_from_shape(box())
    assert mesh.is_watertight and mesh.volume == pytest.approx(18000, rel=1e-6)


def test_fillets_do_not_cause_a_false_sealed_failure():
    # build123d leaves zero-area slivers at fillet corners; they must not read as open edges.
    with BuildPart() as part:
        Box(40, 30, 15, align=BED)
        fillet(part.edges(), 2)
    assert results(run_checks(part.part))["sealed"]["status"] == "pass"


def test_inside_out_mesh_fails_sealed():
    mesh = trimesh.creation.box((10, 10, 10))
    mesh.invert()
    assert results(check_part(mesh, dict(DEFAULTS)))["sealed"]["status"] == "fail"


def test_empty_mesh_fails_sealed_without_crashing():
    mesh = trimesh.Trimesh(vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64))
    report = check_part(mesh, dict(DEFAULTS))
    found = results(report)
    assert found["sealed"]["status"] == "fail" and "no faces" in found["sealed"]["message"]
    assert [r["status"] for r in report["results"][1:]] == ["skipped"] * 5 and report["status"] == "fail"


# --- size and bed_fit ---------------------------------------------------------------------------

def test_size_pass_fail_and_skip():
    assert results(run_checks(box(), (40, 30, 15)))["size"]["status"] == "pass"
    wrong = results(run_checks(box(), (40, 30, 20)))["size"]
    assert wrong["status"] == "fail" and "Z 15.000 vs 20.000" in wrong["message"]
    assert wrong["measured"] == [40.0, 30.0, 15.0] and wrong["expected"] == [40.0, 30.0, 20.0]
    skipped = results(run_checks(box()))["size"]
    assert skipped["status"] == "skipped" and "expected(p)" in skipped["message"]


def test_size_tolerance_is_applied():
    assert results(run_checks(box(), (40.05, 30, 15)))["size"]["status"] == "pass"
    assert results(run_checks(box(), (40.05, 30, 15), size_tol=0.01))["size"]["status"] == "fail"


def test_bed_fit_uses_margin_and_height():
    found = results(run_checks(box(300, 10, 10)))["bed_fit"]
    assert found["status"] == "fail" and "X 300.0 > 246.0" in found["message"]
    assert found["limit"] == [246.0, 246.0, 256.0]
    assert results(run_checks(box(246, 246, 256)))["bed_fit"]["status"] == "pass"
    assert results(run_checks(box(10, 10, 260)))["bed_fit"]["status"] == "fail"


# --- part status --------------------------------------------------------------------------------

def test_worst_status_ignores_skipped():
    assert worst_status(["pass", "skipped"]) == "pass"
    assert worst_status(["pass", "warn"]) == "warn"
    assert worst_status(["warn", "fail", "pass"]) == "fail"
    assert worst_status(["skipped"]) == "pass"
    assert worst_status([]) == "pass"
