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


def test_snap_without_engagement_range_has_no_target(boxlid):
    unit, items, _ = boxlid
    snap = dataclasses.replace(interface(unit, "snap-front"), engagement=None)
    result = ac.check_interface(snap, items)
    assert result["status"] == "fail"
    assert result["message"].startswith(
        "no target: snap-front has no engagement range; ask the user (fixture.md § Snap)")
    assert result["measured"]["engagement"] == pytest.approx(0.35, abs=0.05)  # still measured and reported


def test_snap_without_clearance_range_still_passes(boxlid):
    unit, items, _ = boxlid
    snap = dataclasses.replace(interface(unit, "snap-front"), clearance=None)
    result = ac.check_interface(snap, items)
    assert result["status"] == "pass", result["message"]
    assert result["expected"]["clearance"] is None


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


# --- passage: openings in one wall, oblique walls -----------------------------------------------------------------


def oblique_wall():
    """A 2 mm wall at 60 degrees to x (its normal 30 degrees from x) with a 14 x 5 slot about the origin."""
    from build123d import Rot

    return Rot(0, 0, 30) * (Box(2, 40, 20) - Box(2, 14, 5))


def test_passage_through_oblique_wall_slot():
    items = {"wall": stand_in("wall", oblique_wall()), "rod": stand_in("rod", Box(30, 10, 3))}
    result = passage(items, ["wall"])
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["clearance"]["wall"] == pytest.approx(0.56, abs=0.02)


def test_passage_ending_inside_oblique_slot_not_found():
    # the bar ends at x 1, inside the slanted slot: the wall's far slot end lies beyond it
    items = {"wall": stand_in("wall", oblique_wall()), "rod": stand_in("rod", Pos(-14, 0, 0) * Box(30, 10, 3))}
    result = passage(items, ["wall"])
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of wall")


def test_passage_through_one_wall_of_a_box():
    # a closed box (x -22..22, walls 2 thick), a 6 mm hole in its -x wall; the cable ends inside, near the floor, and
    # the opposite wall lies on its line
    box = Box(44, 30, 20) - Box(40, 26, 16) - Pos(-20, 0, -5) * Cylinder(3, 4, rotation=(0, 90, 0))
    cable = Pos(-15, 0, -5) * Cylinder(2, 30, rotation=(0, 90, 0))  # x -30..0
    items = {"box": stand_in("box", box), "rod": stand_in("rod", cable)}
    result = passage(items, ["box"], direction=(1, 0, 0))
    assert result["status"] == "pass", result["message"]


def tray():
    """A tray: floor (x -30..30, z -2..0), end walls 2 thick and 10 tall, a 12 x 4 slot (z 0.5..4.5) in the -x one."""
    body = Pos(0, 0, -1) * Box(60, 20, 2)
    for x in (-29, 29):
        body += Pos(x, 0, 5) * Box(2, 20, 10)
    return body - Pos(-29, 0, 2.5) * Box(2, 12, 4)


def test_passage_strip_through_tray_end_wall_ending_inside():
    items = {"tray": stand_in("tray", tray()), "rod": stand_in("rod", Pos(-20, 0, 2.5) * Box(40, 10, 3))}  # x -40..0
    result = passage(items, ["tray"], min_clearance=0.4)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["clearance"]["tray"] == pytest.approx(0.5, abs=0.01)


def test_passage_strip_along_a_parallel_wall():
    # the strip runs beside a long side wall (1 mm away) and through the end wall's slot
    body = tray() + Pos(0, 7.5, 5) * Box(56, 2, 10)  # a side wall at y 6.5..8.5
    strip = Pos(-20, 0.5, 2.5) * Box(40, 10, 3)  # y -4.5..5.5
    items = {"tray": stand_in("tray", body), "rod": stand_in("rod", strip)}
    result = passage(items, ["tray"], min_clearance=0.4)
    assert result["status"] == "pass", result["message"]


def test_passage_rod_stops_halfway_through_a_thick_plate():
    plate = Box(20, 20, 10) - Cylinder(3, 10)  # z -5..5
    items = {"plate": stand_in("plate", plate), "rod": stand_in("rod", Pos(0, 0, 10) * Cylinder(2, 20))}  # z 0..20
    result = passage(items, ["plate"])
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of plate")


def test_passage_rod_beside_a_holed_plate_not_found():
    # the plate's corner is cut away (x, y 6..10); the rod stands in that open corner, beside the plate
    plate = Box(20, 20, 3) - Cylinder(3, 3) - Pos(8, 8, 0) * Box(4, 4, 3)
    items = {"plate": stand_in("plate", plate), "rod": stand_in("rod", Pos(8.5, 8.5, 0) * Cylinder(1, 20))}
    result = passage(items, ["plate"])
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of plate")


def test_passage_through_a_gap_between_bodies_not_found():
    # two separate blocks 6 apart, the rod between them: no opening, even though material is on both sides
    from build123d import Compound

    blocks = Compound([Pos(-8, 0, 0) * Box(10, 20, 4), Pos(8, 0, 0) * Box(10, 20, 4)])
    items = {"blocks": stand_in("blocks", blocks), "rod": stand_in("rod", Cylinder(2, 20))}
    result = passage(items, ["blocks"])
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of blocks")


@pytest.mark.parametrize("depth", [0.05, 0.1, 0.19])
def test_passage_shallow_entry_fails(depth):
    # the rod comes down from above and stops `depth` into the plate's hole (plate z -1.5..1.5)
    rod = Pos(0, 0, 1.5 - depth + 10) * Cylinder(2, 20)
    items = {"plate": stand_in("plate", plate_with_hole()), "rod": stand_in("rod", rod)}
    result = passage(items, ["plate"])
    assert result["status"] == "fail"
    assert "it ends inside an opening" in result["message"]


def corner_l(sx, sy, z0, h=2):
    """An L of two 3 mm bars round the origin, at the (sx, sy) corner, z0..z0 + h."""
    return Pos(sx * 4.5, 0, z0 + h / 2) * Box(3, 12, h) + Pos(0, sy * 4.5, z0 + h / 2) * Box(12, 3, h)


def test_passage_separate_bodies_never_make_one_opening():
    from build123d import Compound

    rod = stand_in("rod", Cylinder(2, 20))
    two_ls = Compound([corner_l(-1, -1, 0), corner_l(1, 1, 2)])  # touching depths, but two bodies
    bars = [Pos(-4.5, 0, 1) * Box(3, 12, 2), Pos(0, -4.5, 3) * Box(12, 3, 2), Pos(4.5, 0, 5) * Box(3, 12, 2),
            Pos(0, 4.5, 7) * Box(12, 3, 2)]  # four separate bars at four depths, one on each side
    for shape in (two_ls, Compound(bars)):
        result = passage({"part": stand_in("part", shape), "rod": rod}, ["part"])
        assert result["status"] == "fail"
        assert result["message"].startswith("not found: rod doesn't pass through an opening of part")
    fused = passage({"part": stand_in("part", corner_l(-1, -1, 0) + corner_l(1, 1, 2)), "rod": rod}, ["part"])
    assert fused["status"] == "pass", fused["message"]  # one connected body: a real ring round the rod
    assert fused["measured"]["opening"]["part"] == pytest.approx([0.0, 4.0], abs=1e-3)


# --- passage: ends_inside ----------------------------------------------------------------------------------------


def test_passage_ends_inside_a_slanted_slot_opt_in():
    # the bar runs x -30..0: it ends at the slanted wall's mid-plane, half way into the slot
    items = {"wall": stand_in("wall", oblique_wall()), "rod": stand_in("rod", Pos(-15, 0, 0) * Box(30, 10, 3))}
    default = passage(items, ["wall"])
    assert default["status"] == "fail"
    assert "it ends inside an opening" in default["message"]
    result = passage(items, ["wall"], ends_inside=True)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["aperture"]["wall"] == pytest.approx(5.0, abs=0.05)
    assert result["measured"]["clearance"]["wall"] > 0.5


def test_passage_ends_inside_still_needs_to_reach_the_opening():
    # a holed plate (z -1.5..1.5) and, in the same body, a wall beside the rod's line (x 2.5..4.5, up to z 10): the
    # rod stops 1 mm above the plate, beside the wall, before the hole
    body = plate_with_hole() + Pos(3.5, 0, 5.75) * Box(2, 20, 8.5)
    rod = Pos(0, 0, 12.5) * Cylinder(2, 20)  # z 2.5..22.5
    items = {"plate": stand_in("plate", body), "rod": stand_in("rod", rod)}
    result = passage(items, ["plate"], min_clearance=0.4, ends_inside=True)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of plate")
    # a bare holed plate, the rod ending 1 mm above it
    items = {"plate": stand_in("plate", plate_with_hole()), "rod": stand_in("rod", rod)}
    result = passage(items, ["plate"], ends_inside=True)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found:")


def test_passage_ends_inside_needs_a_real_entry():
    # the rod comes down into a 3 mm plate's hole (z -1.5..1.5): it must enter min(1.0, half the plate) of it
    for depth, status in ((0.05, "fail"), (1.0, "pass")):
        rod = Pos(0, 0, 1.5 - depth + 10) * Cylinder(2, 20)
        items = {"plate": stand_in("plate", plate_with_hole()), "rod": stand_in("rod", rod)}
        result = passage(items, ["plate"], ends_inside=True)
        assert result["status"] == status, (depth, result["message"])
        if status == "fail":
            assert "it enters the opening only 0.05 mm, it needs 1.00 mm" in result["message"]


def test_passage_ends_inside_a_blind_hole():
    # a 6 mm blind hole (z 0..6, floor at z 0) in a block (z -2..6); the rod stops 0.5 mm above the floor
    from build123d import Align

    top = (Align.CENTER, Align.CENTER, Align.MAX)
    block = Pos(0, 0, 6) * (Box(20, 20, 8, align=top) - Cylinder(3, 6, align=top))
    items = {"block": stand_in("block", block), "rod": stand_in("rod", Pos(0, 0, 10.5) * Cylinder(2, 20))}
    assert passage(items, ["block"])["status"] == "fail"
    result = passage(items, ["block"], ends_inside=True)
    assert result["status"] == "pass", result["message"]


def two_slot_tray():
    """tray() with the same 12 x 4 slot (z 0.5..4.5) in its +x end wall (x 28..30) too."""
    return tray() - Pos(29, 0, 2.5) * Box(2, 12, 4)


def test_passage_ends_inside_slots_on_both_sides_of_one_part():
    # one strip across the tray (x -29..29): each end half way into the slot of an end wall (x -30..-28, 28..30)
    items = {"tray": stand_in("tray", two_slot_tray()), "rod": stand_in("rod", Pos(0, 0, 2.5) * Box(58, 10, 3))}
    default = passage(items, ["tray"], min_clearance=0.4)
    assert default["status"] == "fail"
    assert "it ends inside an opening" in default["message"]
    result = passage(items, ["tray"], min_clearance=0.4, ends_inside=True)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["aperture"]["tray"] == pytest.approx(4.0, abs=0.05)
    # one end only 0.05 mm into its slot (x -28.05)
    short = {**items, "rod": stand_in("rod", Pos(0.475, 0, 2.5) * Box(57.05, 10, 3))}
    result = passage(short, ["tray"], min_clearance=0.4, ends_inside=True)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: rod doesn't pass through an opening of tray")


def test_passage_ends_inside_wholly_inside_one_hole_fails():
    # a rod (z -5..5) wholly inside a 20 mm tall block's hole: one opening run, entered from no face
    block = Box(20, 20, 20) - Cylinder(3, 20)
    items = {"block": stand_in("block", block), "rod": stand_in("rod", Cylinder(2, 10))}
    result = passage(items, ["block"], ends_inside=True)
    assert result["status"] == "fail"
    assert "doesn't enter it from a face of the part" in result["message"]


def test_passage_ends_inside_the_material_beside_the_slot_fails():
    # the bar (z 4.5..7.5) ends at the slanted wall's mid-plane, in the wall above the slot (z -2.5..2.5)
    items = {"wall": stand_in("wall", oblique_wall()), "rod": stand_in("rod", Pos(-15, 0, 6) * Box(30, 10, 3))}
    result = passage(items, ["wall"], ends_inside=True)
    assert result["status"] == "fail"
    assert "rod overlaps wall by" in result["message"]


def test_passage_ends_inside_validated():
    assert pa.Passage("p", "rod", ["plate"], 0.5, source="test").ends_inside is False
    assert pa.Passage("p", "rod", ["plate"], 0.5, source="test", ends_inside=True).ends_inside is True
    for bad in ("yes", 1, None):
        with pytest.raises(ValueError, match="ends_inside"):
            pa.Passage("p", "rod", ["plate"], 0.5, source="test", ends_inside=bad)

# --- clear and step -----------------------------------------------------------------------------------------------


def test_clear_fails_on_overlap_whatever_min_distance():
    items = {"a": stand_in("a", Box(10, 10, 10)), "b": stand_in("b", Pos(8, 0, 0) * Box(10, 10, 10))}
    result = ac.check_interface(pa.Clear("c", "a", "b", 0.0, source="test"), items)
    assert result["status"] == "fail"
    assert "a overlaps b by 200 mm^3" in result["message"]
    assert result["measured"]["overlap_mm3"] == pytest.approx(200.0, abs=0.01)
    apart = {**items, "b": stand_in("b", Pos(10, 0, 0) * Box(10, 10, 10))}  # touching faces are not an overlap
    assert ac.check_interface(pa.Clear("c", "a", "b", 0.0, source="test"), apart)["status"] == "pass"


def test_step_on_one_item_measures_both_faces():
    # a stepped block: its top at z 10 for x > 0 and z 8.5 for x < 0
    block = Pos(-5, 0, 4.25) * Box(10, 10, 8.5) + Pos(5, 0, 5) * Box(10, 10, 10)
    items = {"p": stand_in("p", block)}
    step = pa.Step("s", "p", "p", (-5, 0, 20), (5, 0, 20), (0, 0, -1), 1.5, source="test")
    result = ac.check_target(step, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"] == pytest.approx(1.5, abs=1e-6)
    assert result["items"] == ["p"]


def test_step_counts_the_origin_offset():
    # a's top at z 3, b's top at z 5: probed from origins 2 mm apart along the probe, b's face is 2 mm above a's
    items = {"a": stand_in("a", Pos(0, 0, 1.5) * Box(10, 10, 3)), "b": stand_in("b", Pos(20, 0, 2.5) * Box(10, 10, 5))}
    step = pa.Step("s", "a", "b", (0, 0, 10), (20, 0, 12), (0, 0, -1), 2.0, source="test")
    result = ac.check_target(step, items)
    assert result["measured"] == pytest.approx(2.0, abs=1e-6)
    assert result["status"] == "pass", result["message"]
    level = pa.Step("s", "a", "b", (0, 0, 10), (20, 0, 10), (0, 0, -1), 2.0, source="test")  # level origins
    assert ac.check_target(level, items)["measured"] == pytest.approx(2.0, abs=1e-6)
    flush = pa.Step("s", "a", "b", (0, 0, 10), (20, 0, 12), (0, 0, -1), 0.0, source="test")
    assert ac.check_target(flush, items)["status"] == "fail"


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
