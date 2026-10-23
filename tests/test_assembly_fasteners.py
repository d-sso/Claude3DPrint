"""Bolted joints and nut pockets (assembly_checks), on the boxlid fixture and small synthetic joints."""
import dataclasses
import json
import shutil

import pytest
from build123d import Align, Box, Cone, Cylinder, Pos

import assemble_model
import assembly_checks as ac
import print3d_assembly as pa
from conftest import FIXTURES

BOXLID = FIXTURES / "assembly" / "boxlid"
MIN_Z = (Align.CENTER, Align.CENTER, Align.MIN)
KEYS = {"name", "type", "items", "status", "measured", "expected", "source", "location", "message", "evidence", "planes"}


def load(path=BOXLID / "assembly.py", set_pairs=()):
    module, params, shared_overrides = assemble_model.load_assembly(path, set_pairs)
    assembly = assemble_model.declare_assembly(module, params, shared_overrides)
    result = {"warnings": []}
    builder = assemble_model.Builder(path.parent, shared_overrides, result)
    items = assemble_model.build_items(assembly, path.parent, shared_overrides, result, builder=builder)
    unit = assembly.units[0]
    return unit, items[unit.name], builder


@pytest.fixture(scope="module")
def boxlid():
    return load()


def interface(unit, name):
    return next(i for i in unit.interfaces if i.name == name)


def with_part_params(boxlid, name, **params):
    unit, items, builder = boxlid
    decl = unit.item(name)
    return {**items, name: builder.item(dataclasses.replace(decl, params={**decl.params, **params}))}


def with_hardware(items, decl):
    return {**items, decl.name: assemble_model.place_simple(decl)}


def test_boxlid_bolts_pass(boxlid):
    unit, items, _ = boxlid
    for name in ("bolt-left", "bolt-right"):
        result = ac.check_interface(interface(unit, name), items)
        assert set(result) == KEYS
        assert result["status"] == "pass", result["message"]
        assert result["type"] == "bolted"
        assert result["items"] == [f"screw-{name[5:]}", "lid", "base", f"nut-{name[5:]}"]
        assert result["source"] == "fixture.md § Box"
        assert result["evidence"] == []
        for part in ("lid", "base"):
            assert result["measured"]["offset"][part] <= 0.05
            assert result["measured"]["diameter"][part] == pytest.approx(3.6, abs=0.05)
        assert result["measured"]["head_gap"] == pytest.approx(0.0, abs=0.01)
        assert result["measured"]["protrusion"] == pytest.approx(0.9, abs=0.01)
        assert result["measured"]["shank_overlap_mm3"] == {}
        assert result["measured"]["hole_span"]["lid"] == pytest.approx([-3.0, 0.0], abs=1e-3)
        x = -14.0 if name == "bolt-left" else 14.0
        assert result["location"] == pytest.approx([x, 0.0, 17.0])
        assert len(result["planes"]) == 2
        json.dumps(result)  # JSON-ready


def test_hole_offset_fails(boxlid):
    unit, _, _ = boxlid
    items = with_part_params(boxlid, "lid", hole_dx=0.5)
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    assert result["status"] == "fail"
    assert "hole offset" in result["message"] and "lid" in result["message"]
    assert result["measured"]["offset"]["lid"] == pytest.approx(0.5, abs=0.05)


def test_missing_hole_not_found(boxlid):
    unit, _, _ = boxlid
    items = with_part_params(boxlid, "lid", holes=False)
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: no hole on the screw axis in lid")
    assert result["measured"]["offset"]["lid"] is None


def test_screw_too_short():
    unit, items, _ = load(set_pairs=["screw_len=6"])
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    assert result["status"] == "fail"
    assert "screw too short: tip 5.1 mm short of the nut's far face" in result["message"]
    assert result["measured"]["protrusion"] == pytest.approx(-5.1, abs=0.01)


def test_protrusion_rule_from_declaration(boxlid):
    unit, items, _ = boxlid
    bolt = dataclasses.replace(interface(unit, "bolt-left"), protrusion=1.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "screw too short: it protrudes 0.9 mm past the nut's far face, needs 1" in result["message"]
    assert result["expected"]["protrusion_min"] == 1.0


def test_head_gap_fails(boxlid):
    unit, items, _ = boxlid
    screw = unit.item("screw-left")
    lifted = with_hardware(items, dataclasses.replace(screw, origin=(-14.0, 0.0, 23.5)))
    result = ac.check_interface(interface(unit, "bolt-left"), lifted)
    assert result["status"] == "fail"
    assert "head 0.5 mm above the part surface" in result["message"]
    assert result["measured"]["head_gap"] == pytest.approx(0.5, abs=0.01)


def test_clearance_hole_too_small(tmp_path):
    root = tmp_path / "boxlid"
    shutil.copytree(BOXLID, root, ignore=shutil.ignore_patterns("__pycache__", "out"))
    lid = root / "parts" / "lid" / "lid.py"
    text = lid.read_text(encoding="utf-8")
    assert text.count('Cylinder(screw_clearance("M3") / 2, PLATE') == 1
    lid.write_text(text.replace('Cylinder(screw_clearance("M3") / 2, PLATE', "Cylinder(3.0 / 2, PLATE"), encoding="utf-8")
    unit, items, _ = load(root / "assembly.py")
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    assert result["status"] == "fail"
    assert "lid: clearance hole diameter 3 mm" in result["message"]
    assert result["measured"]["diameter"]["lid"] == pytest.approx(3.0, abs=0.05)
    assert result["expected"]["diameter"]["lid"] == {"min": pytest.approx(3.5)}


def test_nut_pockets_pass(boxlid):
    unit, items, _ = boxlid
    for name in ("nut-left", "nut-right"):
        result = ac.check_interface(interface(unit, name), items)
        assert set(result) == KEYS
        assert result["status"] == "pass", result["message"]
        assert result["type"] == "nut_pocket"
        assert result["measured"]["overlap_mm3"] == 0.0
        assert result["measured"]["offset"] <= 0.05
        assert result["measured"]["pocket_width"] < result["expected"]["pocket_width_max"]
        assert result["expected"]["pocket_width_max"] == pytest.approx(5.5 / (3 ** 0.5 / 2), abs=1e-3)
        json.dumps(result)


def test_nut_too_big_for_pocket(boxlid):
    unit, items, _ = boxlid
    big = with_hardware(items, dataclasses.replace(unit.item("nut-left"), size="M4"))
    result = ac.check_interface(interface(unit, "nut-left"), big)
    assert result["status"] == "fail"
    assert "nut-left: the nut overlaps base by" in result["message"]
    assert result["measured"]["overlap_mm3"] > 1.0


def test_round_pocket_lets_nut_turn():
    # a 7 mm round pocket around an M3 nut (across corners 6.35): no overlap, but the nut can turn
    block = Box(20, 20, 6, align=MIN_Z) - Cylinder(3.5, 3, align=MIN_Z) - Cylinder(1.8, 6, align=MIN_Z)
    nut = pa.Nut("nut", "M3", "hex", origin=(0, 0, 0.3), direction=(0, 0, 1))
    items = {"block": stand_in("block", block), "nut": assemble_model.place_simple(nut)}
    result = ac.check_interface(pa.NutPocket("pocket", "nut", "block", source="test"), items)
    assert result["status"] == "fail"
    assert "the nut can turn" in result["message"]
    assert result["measured"]["pocket_width"] == pytest.approx(7.0, abs=0.02)


def stand_in(name, shape):
    return assemble_model.place_simple(pa.StandIn(name, shape))


def countersunk_joint(top_z):
    """An M3x10 countersunk screw (head top at 10) through a 6 mm plate (z 4..10 shifted by top_z - 10) with a 90°
    sink, into a 4 mm thread block with a 2.5 mm pilot under it."""
    plate = Pos(0, 0, top_z - 6) * Box(20, 20, 6, align=MIN_Z)
    plate -= Pos(0, 0, top_z - 6) * Cylinder(1.8, 6, align=MIN_Z)
    plate -= Pos(0, 0, top_z - 1.5) * Cone(1.5, 3.0, 1.5, align=MIN_Z)  # 90 degrees: radius 1.5 -> 3.0 over 1.5
    base = Pos(0, 0, top_z - 10) * (Box(20, 20, 4, align=MIN_Z) - Cylinder(1.25, 4, align=MIN_Z))
    screw = pa.Screw("screw", "M3", "countersunk", 10.0, origin=(0, 0, 10), direction=(0, 0, 1))
    items = {"plate": stand_in("plate", plate), "base": stand_in("base", base),
             "screw": assemble_model.place_simple(screw)}
    return items, pa.Bolted("joint", "screw", [("plate", "clearance"), ("base", "thread")], source="test")


def test_countersunk_seat_pass():
    items, bolt = countersunk_joint(10.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["head_overlap_mm3"] == {"plate": 0.0, "base": 0.0}
    assert result["measured"]["head_flush"] == pytest.approx(0.0, abs=0.01)


def test_countersunk_proud_fails():
    items, bolt = countersunk_joint(9.5)  # the sink is 0.5 mm lower than the head: the head stands proud
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "countersunk head" in result["message"]
    assert result["measured"]["head_flush"] == pytest.approx(-0.5, abs=0.01)


def test_bolted_with_nothing_engaging_the_screw_fails():
    # an M3x12 through a 3 mm clearance plate: no nut, no thread or insert part holds it
    items, bolt = joint({"plate": (holed_plate(10), "clearance")}, 12.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: nothing engages screw (no nut, thread or insert part)")


def test_shank_overlap_and_thread_role():
    # a 2.5 mm pilot hole: as a thread it is skipped by the diameter and shank checks, as a clearance it fails both
    block = Box(20, 20, 10, align=MIN_Z) - Cylinder(1.25, 10, align=MIN_Z)
    screw = pa.Screw("screw", "M3", "socket", 8.0, origin=(0, 0, 10), direction=(0, 0, 1))
    items = {"block": stand_in("block", block), "screw": assemble_model.place_simple(screw)}
    thread = ac.check_interface(pa.Bolted("j", "screw", [("block", "thread")], source="test"), items)
    assert thread["status"] == "pass", thread["message"]
    assert thread["expected"]["diameter"]["block"] is None
    clear = ac.check_interface(pa.Bolted("j", "screw", [("block", "clearance")], source="test"), items)
    assert clear["status"] == "fail"
    assert "screw: shank overlaps block by" in clear["message"]
    assert "block: clearance hole diameter 2.5 mm" in clear["message"]


# --- the screw's own extent along the axis ---------------------------------------------------------------------

CLEAR = 3.6  # screw_clearance("M3") in the base profile


def holed_plate(z0, thickness=3.0, width=20.0, hole_d=CLEAR):
    return Pos(0, 0, z0) * Box(width, 20, thickness, align=MIN_Z) - Pos(0, 0, z0) * Cylinder(hole_d / 2, thickness,
                                                                                               align=MIN_Z)


def joint(parts, length, top=13.0, nut=None):
    screw = pa.Screw("screw", "M3", "socket", length, origin=(0, 0, top), direction=(0, 0, 1))
    items = {name: stand_in(name, shape) for name, (shape, _) in parts.items()}
    items["screw"] = assemble_model.place_simple(screw)
    if nut is not None:
        items["nut"] = assemble_model.place_simple(nut)
    through = [(name, role) for name, (_, role) in parts.items()]
    return items, pa.Bolted("j", "screw", through, nut="nut" if nut else None, source="test")


def test_hole_past_the_tip_beside_a_wall_not_found():
    # a bracket: a wall beside the screw (t -13..-3) and a holed foot 40 mm down (t -43..-40), M3x12 tip at -12
    wall = Pos(2.65, -10, 0) * Box(5, 20, 10, align=(Align.MIN, Align.MIN, Align.MIN))
    column = Pos(20, -10, -30) * Box(3, 20, 40, align=(Align.MIN, Align.MIN, Align.MIN))
    bracket = wall + column + holed_plate(-30, width=46)
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "br": (bracket, "clearance")}, 12.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: no hole on the screw axis in br")
    assert result["measured"]["hole_span"]["br"] is None


def test_part_beyond_the_tip_not_found():
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "far": (holed_plate(-30), "clearance")}, 6.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: no hole on the screw axis in far (no material within")


def test_thread_part_the_screw_never_reaches():
    # an M3x6 (tip at t -6) and a thread part at t -13..-8
    base = Box(20, 20, 5, align=MIN_Z) - Cylinder(1.25, 5, align=MIN_Z)
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "base": (base, "thread")}, 6.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "not found: no hole on the screw axis in base" in result["message"]


def test_thread_engagement_too_short():
    # an M3x9 tip at t -9 enters the thread part (t -13..-8) by 1.0 mm, needs 2 x 0.5
    base = Box(20, 20, 5, align=MIN_Z) - Cylinder(1.25, 5, align=MIN_Z)
    ok_items, ok_bolt = joint({"lid": (holed_plate(10), "clearance"), "base": (base, "thread")}, 10.0)
    ok = ac.check_interface(ok_bolt, ok_items)
    assert ok["status"] == "pass", ok["message"]
    assert ok["measured"]["engagement"]["base"] == pytest.approx(2.0, abs=1e-3)
    assert ok["expected"]["engagement_min"]["base"] == 1.0
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "base": (base, "thread")}, 8.5)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "base: screw engages 0.5 mm of the thread hole, needs 1" in result["message"]


def test_blind_insert_hole_passes():
    # an insert hole 6 deep in a 10 mm block, an M3x5 screw through a 3 mm lid: the floor is past the tip
    import print3d_profile

    d = print3d_profile.insert_hole("M3")
    block = Box(20, 20, 10, align=MIN_Z) - Pos(0, 0, 4) * Cylinder(d / 2, 6, align=MIN_Z)
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "block": (block, "insert")}, 5.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["diameter"]["block"] == pytest.approx(d, abs=0.05)
    assert result["expected"]["diameter"]["block"] == {"nominal": d, "tol": 0.1}
    wrong = Box(20, 20, 10, align=MIN_Z) - Pos(0, 0, 4) * Cylinder(d / 2 + 0.3, 6, align=MIN_Z)
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "block": (wrong, "insert")}, 5.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "block: insert hole diameter" in result["message"]


def test_blind_hole_too_shallow_for_screw_fails():
    # the pilot hole is 3 deep (t -6..-3, floor at t -6): the M3x8 tip at t -8 runs 2 mm into the floor
    block = Box(20, 20, 10, align=MIN_Z) - Pos(0, 0, 7) * Cylinder(1.25, 3, align=MIN_Z)
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "block": (block, "thread")}, 8.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "not found: no hole on the screw axis in block (blocked" in result["message"]


def test_nut_above_the_head_not_on_shank(boxlid):
    unit, items, _ = boxlid
    nut = dataclasses.replace(unit.item("nut-left"), origin=(-14.0, 0.0, 30.0))
    result = ac.check_interface(interface(unit, "bolt-left"), with_hardware(items, nut))
    assert result["status"] == "fail"
    assert "nut-left: the nut is not on the shank" in result["message"]
    assert result["measured"]["nut_span"] == pytest.approx([7.0, 9.4], abs=1e-3)


def test_nut_off_the_shank_axis(boxlid):
    unit, items, _ = boxlid
    nut = dataclasses.replace(unit.item("nut-left"), origin=(-13.5, 0.0, 11.9))
    result = ac.check_interface(interface(unit, "bolt-left"), with_hardware(items, nut))
    assert result["status"] == "fail"
    assert "nut-left: nut axis 0.5 mm from the screw axis" in result["message"]


# --- thread pilot (R11) ------------------------------------------------------------------------------------------


def test_thread_counterbore_over_pilot_screw_ends_in_counterbore():
    # a 3.4 counterbore (t -8..-3) over a 2.5 pilot (t -13..-8): the M3x6 tip at t -6 never reaches the pilot
    base = (Box(20, 20, 10, align=MIN_Z) - Cylinder(1.25, 10, align=MIN_Z)
            - Pos(0, 0, 5) * Cylinder(1.7, 5, align=MIN_Z))
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "base": (base, "thread")}, 6.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: no pilot hole smaller than M3 in base")
    # a longer screw reaches 2 mm into the pilot: it engages
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "base": (base, "thread")}, 10.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["engagement"]["base"] == pytest.approx(2.0, abs=1e-3)


def test_thread_part_with_clearance_hole_only():
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "base": (holed_plate(0, thickness=10), "thread")},
                        10.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert result["message"].startswith("not found: no pilot hole smaller than M3 in base")


def test_thread_proper_pilot_passes():
    base = Box(20, 20, 10, align=MIN_Z) - Cylinder(1.25, 10, align=MIN_Z)
    items, bolt = joint({"lid": (holed_plate(10), "clearance"), "base": (base, "thread")}, 8.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["engagement"]["base"] == pytest.approx(5.0, abs=1e-3)


# --- nut angle ----------------------------------------------------------------------------------------------------


def hex_prism(af, height, angle_deg=0.0, z0=0.0, dx=0.0):
    """A hex prism (across flats af) from z0 up, its corners at angle_deg + k * 60 degrees about +Z."""
    import math

    from build123d import Face, Location, Solid, Vector, Wire

    r = af / math.sqrt(3)
    pts = [Vector(r * math.cos(math.radians(60 * i + angle_deg)), r * math.sin(math.radians(60 * i + angle_deg)), 0)
           for i in range(6)]
    prism = Solid.extrude(Face(Wire.make_polygon(pts, close=True)), Vector(0, 0, height))
    return prism.moved(Location((dx, 0, z0)))


def test_nut_angle_aligns_with_a_turned_pocket():
    # a hex pocket turned 30 degrees: the nut's corners (along +x at angle 0) hit its flats
    block = Box(20, 20, 6, align=MIN_Z) - hex_prism(5.7, 3, angle_deg=30) - Cylinder(1.8, 6, align=MIN_Z)
    items = {"block": stand_in("block", block)}
    pocket = pa.NutPocket("pocket", "nut", "block", source="test")
    for angle, status in ((0.0, "fail"), (30.0, "pass")):
        nut = pa.Nut("nut", "M3", "hex", origin=(0, 0, 0.3), direction=(0, 0, 1), angle=angle)
        result = ac.check_interface(pocket, with_hardware(items, nut))
        assert result["status"] == status, result["message"]
        if status == "fail":
            assert "nut: the nut overlaps block by" in result["message"]
        else:
            assert result["measured"]["overlap_mm3"] == 0


def test_nut_angle_validated():
    with pytest.raises(ValueError, match="angle"):
        pa.Nut("nut", "M3", "hex", origin=(0, 0, 0), direction=(0, 0, 1), angle="30")
    assert pa.Nut("nut", "M3", "hex", origin=(0, 0, 0), direction=(0, 0, 1), angle=15).angle == 15.0


# --- washers ------------------------------------------------------------------------------------------------------


def washer_joint(lift=0.0, declare=True):
    """An M3x6 socket screw on an M3 washer (0.5 thick) on a 3 mm plate (z 10..13), its tip in a thread block with a
    2.5 mm pilot under the plate (z 6..10); `lift` raises washer and screw."""
    top = 13.0 + lift
    screw = pa.Screw("screw", "M3", "socket", 6.0, origin=(0, 0, top + 0.5), direction=(0, 0, 1))
    washer = pa.Washer("washer", "M3", origin=(0, 0, top), direction=(0, 0, 1))
    base = Pos(0, 0, 6) * (Box(20, 20, 4, align=MIN_Z) - Cylinder(1.25, 4, align=MIN_Z))
    items = {"plate": stand_in("plate", holed_plate(10)), "base": stand_in("base", base),
             "screw": assemble_model.place_simple(screw), "washer": assemble_model.place_simple(washer)}
    bolt = pa.Bolted("j", "screw", [("plate", "clearance"), ("base", "thread")],
                     washer="washer" if declare else None, source="test")
    return items, bolt


def test_washer_under_the_head_passes():
    items, bolt = washer_joint()
    result = ac.check_interface(bolt, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["head_gap"] == pytest.approx(0.0, abs=1e-3)
    assert result["measured"]["washers"]["washer"]["gap"] == pytest.approx(0.0, abs=1e-3)
    assert "washer" in result["items"]


def test_lifted_washer_fails_on_its_seat():
    items, bolt = washer_joint(lift=0.5)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "washer: washer 0.5 mm from the part surface" in result["message"]
    assert result["measured"]["head_gap"] == pytest.approx(0.0, abs=1e-3)


def test_undeclared_washer_reads_as_a_head_gap():
    items, bolt = washer_joint(declare=False)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "screw: head 0.5 mm above the part surface" in result["message"]


def test_washer_off_axis_fails():
    items, bolt = washer_joint()
    moved = pa.Washer("washer", "M3", origin=(0.3, 0, 13.0), direction=(0, 0, 1))
    result = ac.check_interface(bolt, with_hardware(items, moved))
    assert result["status"] == "fail"
    assert "washer: washer axis 0.3 mm from the screw axis" in result["message"]


def test_washer_declarations_validated():
    with pytest.raises(ValueError, match="nut_washer"):
        pa.Bolted("j", "screw", [("lid", "clearance")], nut_washer="w", source="test")
    screw = pa.Screw("screw", "M3", "socket", 6.0, origin=(0, 0, 13), direction=(0, 0, 1))
    nut = pa.Nut("nut", "M3", "hex", origin=(0, 0, 7), direction=(0, 0, 1))
    lid = pa.StandIn("lid", Box(5, 5, 5))
    bolt = pa.Bolted("j", "screw", [("lid", "clearance")], washer="nut", source="test")
    with pytest.raises(ValueError, match="must be a Washer"):
        pa.Unit("u", [screw, nut, lid], interfaces=[bolt])


def test_nut_side_washer_seat():
    # M3x12 down through a 3 mm plate (z 10..13); a washer under the plate (z 9.5..10), the nut below it (z 7.1..9.5)
    screw = pa.Screw("screw", "M3", "socket", 12.0, origin=(0, 0, 13), direction=(0, 0, 1))
    nut = pa.Nut("nut", "M3", "hex", origin=(0, 0, 7.1), direction=(0, 0, 1))
    items = {"plate": stand_in("plate", holed_plate(10)), "screw": assemble_model.place_simple(screw),
             "nut": assemble_model.place_simple(nut)}
    bolt = pa.Bolted("j", "screw", [("plate", "clearance")], nut="nut", nut_washer="w", source="test")
    for z, status in ((9.5, "pass"), (9.0, "fail")):
        w = pa.Washer("w", "M3", origin=(0, 0, z), direction=(0, 0, 1))
        result = ac.check_interface(bolt, with_hardware(items, w))
        assert result["status"] == status, result["message"]
    assert "w: washer 0.5 mm from the part surface" in result["message"]


# --- the nut's depths are not the hole -----------------------------------------------------------------------------


def test_off_centre_nut_tunnel_not_counted_in_hole_offset():
    # a centred clearance hole in a 10 mm block, a hex tunnel 0.12 off-centre for the nut (z 0..2.4)
    block = Box(20, 20, 10, align=MIN_Z) - Cylinder(1.8, 10, align=MIN_Z) - hex_prism(5.8, 2.4, dx=0.12)
    screw = pa.Screw("screw", "M3", "socket", 12.0, origin=(0, 0, 10), direction=(0, 0, 1))
    nut = pa.Nut("nut", "M3", "hex", origin=(0, 0, 0), direction=(0, 0, 1))
    items = {"block": stand_in("block", block), "screw": assemble_model.place_simple(screw),
             "nut": assemble_model.place_simple(nut)}
    bolt = ac.check_interface(pa.Bolted("j", "screw", [("block", "clearance")], nut="nut", source="test"), items)
    assert bolt["status"] == "pass", bolt["message"]
    assert bolt["measured"]["offset"]["block"] <= 0.02
    assert any("nut" in why for _, _, why in bolt["measured"]["excluded"]["block"])
    pocket = ac.check_interface(pa.NutPocket("pocket", "nut", "block", source="test"), items)
    assert pocket["measured"]["offset"] == pytest.approx(0.12, abs=0.02)
    assert pocket["status"] == "fail" and "from the pocket centre" in pocket["message"]
    # without the nut declared on the joint, the tunnel is part of the hole and its offset counts
    bare = ac.check_interface(pa.Bolted("j", "screw", [("block", "clearance")], source="test"), items)
    assert bare["measured"]["offset"]["block"] == pytest.approx(0.12, abs=0.02)


def tunnel_joint(hole_dx=0.0, tunnel_top=2.4):
    """An M3x12 (tip at z -2) down through a block (z -3..10): a clearance hole hole_dx off the axis and a hex tunnel
    0.12 off-centre from z -3 up to tunnel_top; the nut sits in the tunnel at z 0..2.4."""
    block = (Pos(0, 0, -3) * Box(20, 20, 13, align=MIN_Z) - Pos(hole_dx, 0, -3) * Cylinder(1.8, 13, align=MIN_Z)
             - hex_prism(5.8, tunnel_top + 3, z0=-3, dx=0.12))
    screw = pa.Screw("screw", "M3", "socket", 12.0, origin=(0, 0, 10), direction=(0, 0, 1))
    nut = pa.Nut("nut", "M3", "hex", origin=(0, 0, 0), direction=(0, 0, 1))
    items = {"block": stand_in("block", block), "screw": assemble_model.place_simple(screw),
             "nut": assemble_model.place_simple(nut)}
    return items, pa.Bolted("j", "screw", [("block", "clearance")], nut="nut", source="test")


@pytest.mark.parametrize("tunnel_top", [2.4, 5.4])  # past the nut on the tip side; on both sides
def test_nut_tunnel_past_the_nut_not_counted_in_hole_offset(tunnel_top):
    items, bolt = tunnel_joint(tunnel_top=tunnel_top)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["offset"]["block"] <= 0.02
    assert any("tunnel" in why for _, _, why in result["measured"]["excluded"]["block"])
    # the clearance hole beyond the tunnel is still measured
    off_items, off_bolt = tunnel_joint(hole_dx=0.3, tunnel_top=tunnel_top)
    result = ac.check_interface(off_bolt, off_items)
    assert result["status"] == "fail"
    assert "block: hole offset 0.3 mm from the screw axis" in result["message"]


def test_nut_tunnel_measured_when_it_is_all_the_hole():
    # the hex tunnel (0.12 off-centre) runs through the whole block: no narrower hole is left, so the tunnel is measured
    block = Pos(0, 0, -3) * Box(20, 20, 13, align=MIN_Z) - hex_prism(5.8, 13, z0=-3, dx=0.12)
    items, bolt = tunnel_joint()
    items = {**items, "block": stand_in("block", block)}
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert result["measured"]["offset"]["block"] == pytest.approx(0.12, abs=0.02)
    assert not any("tunnel" in why for _, _, why in result["measured"]["excluded"]["block"])


def test_nut_tunnel_only_in_the_nut_pocket_part():
    items, bolt = tunnel_joint()
    pocket = pa.NutPocket("pocket", "nut", "block", source="test")
    assert ac.check_interface(bolt, items, [bolt, pocket])["status"] == "pass"
    # a NutPocket that puts the nut in another part: the block's tunnel is measured as its hole
    elsewhere = pa.NutPocket("pocket", "nut", "other", source="test")
    result = ac.check_interface(bolt, items, [bolt, elsewhere])
    assert result["status"] == "fail"
    assert result["measured"]["offset"]["block"] == pytest.approx(0.12, abs=0.02)


def nut_trap_joint(trap, hole_dx=0.0):
    """An M3x12 through a 7 mm top part (z 3..10) into a nut trap part; the nut at z 0..2.4."""
    top = Pos(0, 0, 3) * Box(20, 20, 7, align=MIN_Z) - Cylinder(1.8, 10, align=MIN_Z)
    screw = pa.Screw("screw", "M3", "socket", 12.0, origin=(0, 0, 10), direction=(0, 0, 1))
    nut = pa.Nut("nut", "M3", "hex", origin=(0, 0, 0), direction=(0, 0, 1))
    items = {"top": stand_in("top", top), "trap": stand_in("trap", trap), "screw": assemble_model.place_simple(screw),
             "nut": assemble_model.place_simple(nut)}
    bolt = pa.Bolted("j", "screw", [("top", "clearance"), ("trap", "clearance")], nut="nut", source="test")
    return ac.check_interface(bolt, items)


def test_nut_trap_in_a_thin_part_measures_what_the_nut_leaves():
    # a 3 mm part (z 0..3) with a centred 2.4 mm nut pocket: only 0.6 mm of it is the screw's hole, and that is enough
    trap = Box(20, 20, 3, align=MIN_Z) - hex_prism(5.8, 2.4) - Cylinder(1.8, 3, align=MIN_Z)
    result = nut_trap_joint(trap)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["hole_span"]["trap"] == pytest.approx([-7.6, -7.0], abs=1e-3)
    # a part z -2..3 whose hole is 0.3 off the axis outside the nut's depths: that hole is measured and fails
    off = (Pos(0, 0, -2) * Box(20, 20, 5, align=MIN_Z) - hex_prism(5.8, 2.4)
           - Pos(0.3, 0, -2) * Cylinder(1.8, 5, align=MIN_Z))
    result = nut_trap_joint(off)
    assert result["status"] == "fail"
    assert "trap: hole offset 0.3 mm from the screw axis" in result["message"]
