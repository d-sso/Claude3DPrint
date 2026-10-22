"""Snaps, seats, passages, clearances, the overlap sweep and intent targets (assembly_checks), on boxlid and small
synthetic stand-ins."""
import dataclasses
import json

import pytest
from build123d import Box, Cylinder, Pos

import assemble_model
import assembly_checks as ac
import print3d_assembly as pa
from test_assembly_fasteners import KEYS, interface, load, with_part_params

TARGET_KEYS = KEYS | {"value", "tol"}


@pytest.fixture(scope="module")
def boxlid():
    return load()


def target(unit, name):
    return next(t for t in unit.targets if t.name == name)


def stand_in(name, shape):
    return assemble_model.place_simple(pa.StandIn(name, shape))


def intended(unit, items):
    return ac.intended_overlaps(unit.interfaces)


# --- snap ---------------------------------------------------------------------------------------------------------


def test_snaps_pass(boxlid):
    unit, items, _ = boxlid
    for name in ("snap-front", "snap-back"):
        result = ac.check_interface(interface(unit, name), items)
        assert set(result) == KEYS
        assert result["status"] == "pass", result["message"]
        assert result["type"] == "snap"
        assert result["items"] == ["lid", "base"]
        # the 0.5 bump reaches 0.35 past the catch edge: 0.15 of its height is the running clearance
        assert result["measured"]["engagement"] == pytest.approx(0.35, abs=0.05)
        assert result["measured"]["clearance"] == pytest.approx(0.15, abs=0.05)
        assert result["measured"]["overlap_mm3"] == 0
        assert len(result["planes"]) == 1
        json.dumps(result)


def test_snap_without_bump_fails(boxlid):
    unit, _, _ = boxlid
    items = with_part_params(boxlid, "lid", bump_h=0.0)
    result = ac.check_interface(interface(unit, "snap-front"), items)
    assert result["status"] == "fail"
    assert "no engagement" in result["message"]
    assert result["measured"]["engagement"] == 0


def test_snap_engagement_out_of_range(boxlid):
    unit, items, _ = boxlid
    snap = dataclasses.replace(interface(unit, "snap-front"), engagement=(0.5, 0.6))
    result = ac.check_interface(snap, items)
    assert result["status"] == "fail"
    assert "engagement 0.35" in result["message"]


def test_snap_fit_reported(boxlid):
    unit, items, _ = boxlid
    snap = dataclasses.replace(interface(unit, "snap-front"), fit="sliding")
    result = ac.check_interface(snap, items)
    assert result["expected"]["fit_per_side"] is not None


def test_snap_interference_is_reported_and_intended(boxlid):
    unit, _, _ = boxlid
    items = with_part_params(boxlid, "lid", bump_h=0.8)  # the bump now presses into the groove floor
    results = {name: ac.check_interface(interface(unit, name), items) for name in ("snap-front", "snap-back")}
    front = results["snap-front"]
    assert front["status"] == "fail" and "lid overlaps base" in front["message"]
    assert front["measured"]["bump_overlap_mm3"] > 0
    assert front["measured"]["bump_overlap_mm3"] < front["measured"]["overlap_mm3"]  # half is at the back snap
    assert frozenset(("lid", "base")) not in intended(unit, items)  # without the snap results: not intended
    pairs = ac.intended_overlaps(unit.interfaces, results)
    assert frozenset(("lid", "base")) in pairs
    assert ac.overlap_sweep(items, pairs) == []
    # with only one snap's result the other half isn't accounted for: the sweep reports it
    assert frozenset(("lid", "base")) not in ac.intended_overlaps(unit.interfaces, {"snap-front": front})


def test_snap_section_missing_not_found(boxlid):
    unit, items, _ = boxlid
    snap = dataclasses.replace(interface(unit, "snap-front"), point=(0.0, -13.0, 60.0))
    result = ac.check_interface(snap, items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found:")


# --- seat, clear, passage -----------------------------------------------------------------------------------------


def test_lid_seat_pass(boxlid):
    unit, items, _ = boxlid
    result = ac.check_interface(interface(unit, "lid-seat"), items)
    assert set(result) == KEYS
    assert result["status"] == "pass", result["message"]
    assert result["type"] == "seat"
    assert result["measured"]["gap"] == pytest.approx(0.0, abs=0.01)
    assert result["measured"]["overlap_mm3"] == 0


def test_lid_seat_gap_fails(boxlid):
    unit, items, builder = boxlid
    lifted = builder.item(dataclasses.replace(unit.item("lid"), at=pa.place(z=23.5, rx=180)))
    result = ac.check_interface(interface(unit, "lid-seat"), {**items, "lid": lifted})
    assert result["status"] == "fail"
    assert result["measured"]["gap"] == pytest.approx(0.5, abs=0.01)


def test_seat_with_nothing_near_not_found(boxlid):
    unit, items, _ = boxlid
    seat = dataclasses.replace(interface(unit, "lid-seat"), point=(0.0, -14.0, 50.0))
    result = ac.check_interface(seat, items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found:")


def test_battery_clear_and_overlap(boxlid):
    unit, items, _ = boxlid
    result = ac.check_interface(interface(unit, "battery-clear"), items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["distance"] == pytest.approx(2.0, abs=0.01)
    moved = dataclasses.replace(unit.item("battery"), at=pa.place(x=15, z=6, rz=90))
    shifted = {**items, "battery": assemble_model.place_simple(moved)}
    result = ac.check_interface(interface(unit, "battery-clear"), shifted)
    assert result["status"] == "fail"
    assert result["measured"]["distance"] == 0
    sweep = ac.overlap_sweep(shifted, intended(unit, shifted))
    assert [{e["a"], e["b"]} for e in sweep] == [{"battery", "base"}]
    entry = sweep[0]
    assert set(entry) >= {"a", "b", "volume_mm3", "location", "planes"}
    assert entry["volume_mm3"] > 1
    json.dumps(sweep)


def plate_with_hole():
    return Box(20, 20, 3) - Cylinder(3, 3)


@pytest.mark.parametrize(
    "rod_d, dx, min_clearance, status, text",
    [
        (4.0, 0.0, 0.5, "pass", ""),
        (5.5, 0.0, 0.5, "fail", "plate: clearance 0.25"),
        (4.0, 2.0, 0.5, "fail", "rod overlaps plate"),
        (4.0, 40.0, 0.5, "fail", "not found:"),
    ],
)
def test_passage(rod_d, dx, min_clearance, status, text):
    rod = Pos(dx, 0, 0) * Cylinder(rod_d / 2, 20)
    items = {"plate": stand_in("plate", plate_with_hole()), "rod": stand_in("rod", rod)}
    passage = pa.Passage("cable", "rod", ["plate"], min_clearance, source="test")
    result = ac.check_interface(passage, items)
    assert set(result) == KEYS
    assert result["status"] == status, result["message"]
    assert text in result["message"]


def passage(items, through, min_clearance=0.5, **kw):
    return ac.check_interface(pa.Passage("cable", "rod", through, min_clearance, source="test", **kw), items)


def test_passage_beside_part_not_found():
    # an L-bracket without any hole; the rod stands in its inside corner, through nothing
    bracket = Pos(0, 0, -1.5) * Box(20, 20, 3) + Pos(-8.5, 0, 8.5) * Box(3, 20, 20)
    items = {"bracket": stand_in("bracket", bracket), "rod": stand_in("rod", Pos(2, 0, 10) * Cylinder(2, 12))}
    result = passage(items, ["bracket"])
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of bracket")


def test_passage_stops_short_not_found():
    # the rod runs z 0.5..20.5: it ends inside the plate's hole (plate z -1.5..1.5)
    items = {"plate": stand_in("plate", plate_with_hole()), "rod": stand_in("rod", Pos(0, 0, 10.5) * Cylinder(2, 20))}
    result = passage(items, ["plate"])
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of plate")


def test_passage_through_slot_passes():
    # a 10 x 3 strip along x through closed 12 x 4 slots in four blocks in a row
    blocks = {f"block{k}": stand_in(f"block{k}", Pos(20 * k, 0, 0) * (Box(4, 20, 12) - Box(4, 12, 4)))
              for k in range(4)}
    items = {**blocks, "rod": stand_in("rod", Pos(30, 0, 0) * Box(80, 10, 3))}
    result = passage(items, list(blocks), min_clearance=0.4)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["direction"] == [1.0, 0.0, 0.0]
    for name in blocks:
        assert result["measured"]["clearance"][name] == pytest.approx(0.5, abs=0.01)
    solid = {**items, "block2": stand_in("block2", Pos(40, 0, 0) * Box(4, 20, 12))}
    result = passage(solid, list(blocks), min_clearance=0.4)
    assert result["status"] == "fail" and "block2" in result["message"]


def test_passage_direction_declared():
    # a short, wide disc through a hole: its longest bounding-box axis lies across the hole, not along it
    plate = Box(20, 20, 3) - Cylinder(4, 3)
    items = {"plate": stand_in("plate", plate), "rod": stand_in("rod", Cylinder(3, 5))}
    assert passage(items, ["plate"])["status"] == "fail"
    result = passage(items, ["plate"], direction=(0, 0, 2))
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["direction"] == [0.0, 0.0, 1.0]
    assert result["planes"][0][1] == [0.0, 0.0, 1.0]


# --- overlap sweep ------------------------------------------------------------------------------------------------


def test_sweep_clean_boxlid(boxlid):
    unit, items, _ = boxlid
    assert ac.overlap_sweep(items, intended(unit, items)) == []


def test_intended_pairs_only_screws_in_threads():
    block = pa.StandIn("block", Box(10, 10, 10))
    screw = pa.Screw("screw", "M3", "socket", 8, origin=(0, 0, 5), direction=(0, 0, 1))
    nut = pa.Nut("nut", "M3", "hex", origin=(20, 0, 0), direction=(0, 0, 1))
    bolted = pa.Bolted("joint", "screw", [("block", "thread")], source="test")
    clear = pa.Bolted("joint2", "screw", [("block", "clearance")], nut="nut", source="test")
    assert ac.intended_overlaps([bolted]) == {frozenset(("screw", "block"))}
    assert ac.intended_overlaps([clear]) == set()
    items = {"block": stand_in("block", block.shape), "screw": assemble_model.place_simple(screw),
             "nut": assemble_model.place_simple(nut)}
    assert ac.overlap_sweep(items, ac.intended_overlaps([bolted])) == []
    sweep = ac.overlap_sweep(items, set())
    assert [{e["a"], e["b"]} for e in sweep] == [{"block", "screw"}]


# --- targets ------------------------------------------------------------------------------------------------------


def test_targets_pass_and_disagree(boxlid):
    unit, items, _ = boxlid
    height = target(unit, "height")
    result = ac.check_target(height, items)
    assert set(result) == TARGET_KEYS
    assert result["status"] == "pass", result["message"]
    assert result["type"] == "size"
    assert result["measured"] == pytest.approx(23.0, abs=0.01)
    result = ac.check_target(dataclasses.replace(height, value=24.0), items)
    assert result["status"] == "fail"
    assert result["measured"] == pytest.approx(23.0, abs=0.01)
    assert result["value"] == 24.0 and result["tol"] == pytest.approx(0.1)
    assert "disagree" in result["message"]
    flush = ac.check_target(target(unit, "lid-flush"), items)
    assert flush["status"] == "pass", flush["message"]
    assert flush["measured"] == pytest.approx(0.0, abs=0.01)
    json.dumps(result)


def test_missing_target_value_fails(boxlid):
    unit, items, _ = boxlid
    result = ac.check_target(dataclasses.replace(target(unit, "height"), value=None), items)
    assert result["status"] == "fail"
    assert result["message"].startswith("no target:")
    assert result["measured"] == pytest.approx(23.0, abs=0.01)


def test_gap_and_position_targets(boxlid):
    _, items, _ = boxlid
    smallest = pa.Gap("battery-gap", "battery", "base", None, 2.0, source="test")
    assert ac.check_target(smallest, items)["measured"] == pytest.approx(2.0, abs=0.01)
    signed = pa.Gap("battery-floor", "battery", "base", (0, 0, -1), 2.0, source="test")
    result = ac.check_target(signed, items)
    assert result["status"] == "pass", result["message"]
    position = pa.Position("lid-edge", "lid", (40, 0, 21.5), (-1, 0, 0), 20.0, source="test")
    result = ac.check_target(position, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"] == pytest.approx(20.0, abs=0.01)
    miss = pa.Position("nothing", "lid", (40, 0, 60), (-1, 0, 0), 20.0, source="test")
    assert ac.check_target(miss, items)["message"].startswith("not found:")
