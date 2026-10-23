"""Layouts (assemble_model.check_layout): checks across unit instances, the exact subset and placement-only targets."""
import dataclasses

import pytest
from build123d import Box

import assemble_model
import print3d_assembly as pa
from conftest import FIXTURES

ROW = FIXTURES / "assembly" / "row" / "assembly.py"
LAYOUT_KEYS = {"name", "instances", "exact", "interfaces", "overlaps", "targets", "skipped"}


@pytest.fixture(scope="module")
def row():
    module, params, shared_overrides = assemble_model.load_assembly(ROW)
    assembly = assemble_model.declare_assembly(module, params, shared_overrides)
    result = {"warnings": []}
    builder = assemble_model.Builder(ROW.parent, shared_overrides, result)
    items = assemble_model.build_items(assembly, ROW.parent, shared_overrides, result, builder=builder)
    return assembly, items, builder


def check(row, **changes):
    assembly, items, builder = row
    layout = dataclasses.replace(assembly.layouts[0], **changes) if changes else assembly.layouts[0]
    return assemble_model.check_layout(layout, items, builder, units=assembly.units)


def by_name(entries, name):
    return next(e for e in entries if e["name"] == name)


def test_row_layout_passes(row):
    result = check(row)
    assert set(result) == LAYOUT_KEYS
    assert result["name"] == "row"
    assert result["instances"] == 4
    assert result["exact"] == ["c1", "c2"]
    strip = by_name(result["interfaces"], "strip")
    assert strip["status"] == "pass", strip["message"]
    for k in range(1, 5):
        assert strip["measured"]["clearance"][f"c{k}.block"] == pytest.approx(0.5, abs=0.01)
    seam = by_name(result["targets"], "seam-12")
    assert seam["status"] == "pass", seam["message"]
    assert seam["measured"] == pytest.approx(0.4, abs=0.01)
    length = by_name(result["targets"], "row-length")
    assert length["status"] == "pass", length["message"]
    assert length["measured"] == pytest.approx(119.6, abs=0.01)
    assert result["overlaps"] == []
    assert result["skipped"] == []


def test_passage_blocked_in_second_unit(row):
    result = check(row, overrides={"c2": {"block": {"slot_dz": 3}}})
    strip = by_name(result["interfaces"], "strip")
    assert strip["status"] == "fail"
    assert "c2.block" in strip["message"]
    assert "c1.block" not in strip["message"]
    # the strip meets the raised slot's material: the sweep (layout items against every part) sees it too
    assert [(o["a"], o["b"]) for o in result["overlaps"]] == [("c2.block", "strip")]


def test_passage_blocked_in_non_exact_unit(row):
    # a layout stand-in is checked against every part it lists, also outside the exact subset
    result = check(row, overrides={"c4": {"block": {"slot_dz": 3}}})
    strip = by_name(result["interfaces"], "strip")
    assert strip["status"] == "fail" and "c4.block" in strip["message"]
    assert any({o["a"], o["b"]} == {"c4.block", "strip"} for o in result["overlaps"])


def test_targets_cover_non_exact_instances(row):
    assembly = row[0]
    seam34 = pa.Gap("seam-34", "c3.block", "c4.block", None, 0.4, source="test")
    result = check(row, targets=assembly.layouts[0].targets + (seam34,))
    seam = by_name(result["targets"], "seam-34")
    assert seam["status"] == "pass", seam["message"]
    assert seam["measured"] == pytest.approx(0.4, abs=0.01)
    wrong = pa.Gap("seam-34", "c3.block", "c4.block", None, 1.0, source="test")
    result = check(row, targets=(wrong,))
    assert by_name(result["targets"], "seam-34")["status"] == "fail"


def test_overlap_sweep_limited_to_exact(row):
    layout = row[0].layouts[0]
    # c2 pushed 1 mm into c1 (both exact), c4 pushed 1 mm into c3 (neither exact)
    moved = {"c2": pa.place(x=29), "c4": pa.place(x=89)}
    instances = [(i, u, moved.get(i, loc)) for i, u, loc in layout.instances]
    result = check(row, instances=instances)
    pairs = [{o["a"], o["b"]} for o in result["overlaps"]]
    assert {"c1.block", "c2.block"} in pairs
    assert {"c3.block", "c4.block"} not in pairs


def test_interfaces_outside_exact_are_skipped(row):
    clear12 = pa.Clear("clear-12", "c1.block", "c2.block", 0.3, source="test")
    clear34 = pa.Clear("clear-34", "c3.block", "c4.block", 0.3, source="test")
    clear_strip = pa.Clear("strip-c4", "strip", "c4.block", 0.3, source="test")
    result = check(row, interfaces=(clear12, clear34, clear_strip))
    assert by_name(result["interfaces"], "clear-12")["status"] == "pass"
    # a layout item is always checked exactly, also against a part outside the exact subset
    assert by_name(result["interfaces"], "strip-c4")["status"] == "pass"
    assert [s["name"] for s in result["skipped"]] == ["clear-34"]
    skipped = result["skipped"][0]
    assert skipped["type"] == "clear" and skipped["items"] == ["c3.block", "c4.block"]
    assert "c3" in skipped["reason"] and "c4" in skipped["reason"] and "exact" in skipped["reason"]
    assert all(r["name"] != "clear-34" for r in result["interfaces"])


def test_skipped_layout_interfaces_warn(row):
    assembly, items, builder = row
    clear34 = pa.Clear("clear-34", "c3.block", "c4.block", 0.3, source="test")
    clear_c4 = pa.Clear("clear-c4", "c4.block", "c3.block", 0.5, source="test")
    layout = dataclasses.replace(assembly.layouts[0], interfaces=(clear34, clear_c4),
                                 overrides={"c3": {"block": {"slot_dz": 0.5}}})  # an instance skip isn't counted
    result = {"warnings": []}
    assemble_model._stage_check(assembly, builder, items, [], [layout], result)
    assert result["warnings"] == [
        "layout row: 2 interfaces skipped (not in the exact subset); they are not checked"]
    result = {"warnings": []}
    one = dataclasses.replace(assembly.layouts[0], interfaces=(clear34,))
    assemble_model._stage_check(assembly, builder, items, [], [one], result)
    assert result["warnings"] == [
        "layout row: 1 interface skipped (not in the exact subset); it is not checked"]
    result = {"warnings": []}
    assemble_model._stage_check(assembly, builder, items, [], [assembly.layouts[0]], result)
    assert result["warnings"] == []


def test_override_outside_exact_is_reported(row):
    result = check(row, overrides={"c3": {"block": {"slot_dz": 0.5}}})
    assert [s["name"] for s in result["skipped"]] == ["c3"]
    assert "exact" in result["skipped"][0]["reason"]


def test_layout_item_name_without_dot():
    # "c1.block" would collide with instance c1's item block in place_layout
    with pytest.raises(ValueError, match="must not contain '.'"):
        pa.Layout("row", [("c1", "u", pa.place())], items=[pa.StandIn("c1.block", Box(1, 1, 1))])


# --- exact variants: an overridden exact instance gets its own unit's checks ------------------------------------

BOXLID = FIXTURES / "assembly" / "boxlid" / "assembly.py"


@pytest.fixture(scope="module")
def boxlid():
    module, params, shared_overrides = assemble_model.load_assembly(BOXLID)
    assembly = assemble_model.declare_assembly(module, params, shared_overrides)
    result = {"warnings": []}
    builder = assemble_model.Builder(BOXLID.parent, shared_overrides, result)
    items = assemble_model.build_items(assembly, BOXLID.parent, shared_overrides, result, builder=builder)
    return assembly, items, builder


def check_boxes(boxlid, lid_params):
    """Two boxes side by side, both exact; b2's lid is a variant."""
    assembly, items, builder = boxlid
    layout = pa.Layout("pair", [("b1", "box", pa.place()), ("b2", "box", pa.place(x=60))], exact=("b1", "b2"),
                       overrides={"b2": {"lid": lid_params}})
    return assemble_model.check_layout(layout, items, builder, units=assembly.units)


def test_exact_variant_unbroken_passes(boxlid):
    result = check_boxes(boxlid, {"hole_dx": 0.0})
    unit = boxlid[0].units[0]
    assert sorted(r["name"] for r in result["interfaces"]) == sorted(f"b2/{i.name}" for i in unit.interfaces)
    assert sorted(r["name"] for r in result["targets"]) == sorted(f"b2/{t.name}" for t in unit.targets)
    for r in result["interfaces"] + result["targets"]:
        assert r["status"] == "pass", (r["name"], r["message"])
        assert r["instance"] == "b2"
    bolt = by_name(result["interfaces"], "b2/bolt-left")
    assert bolt["items"] == ["b2.screw-left", "b2.lid", "b2.base", "b2.nut-left"]
    # measured in the unit frame, reported in the layout's: b2 sits at x = 60, the left bolt at unit x = -14
    assert bolt["location"][0] == pytest.approx(46.0, abs=0.01)
    assert result["overlaps"] == [] and result["skipped"] == []


def test_exact_variant_interfaces_checked(boxlid):
    # the variant's screw holes are 0.5 mm off the screw axis: the variant's own bolted joints must fail on it
    result = check_boxes(boxlid, {"hole_dx": 0.5})
    for side in ("left", "right"):
        bolt = by_name(result["interfaces"], f"b2/bolt-{side}")
        assert bolt["status"] == "fail" and "lid" in bolt["message"]
        assert bolt["measured"]["offset"]["lid"] == pytest.approx(0.5, abs=0.05)
    assert any({o["a"], o["b"]} == {"b2.screw-left", "b2.lid"} for o in result["overlaps"])


def test_exact_variant_snap_not_overlap(boxlid):
    # a taller bump interferes with the groove: the snap checks report it, the sweep treats it as intended
    result = check_boxes(boxlid, {"bump_h": 0.8})
    for side in ("front", "back"):
        snap = by_name(result["interfaces"], f"b2/snap-{side}")
        assert snap["status"] == "fail" and snap["measured"]["overlap_mm3"] > 0
    assert result["overlaps"] == []


def test_units_required(row):
    assembly, items, builder = row
    with pytest.raises(TypeError):
        assemble_model.check_layout(assembly.layouts[0], items, builder)
