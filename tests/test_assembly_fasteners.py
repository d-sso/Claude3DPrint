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
    """An M3 countersunk screw (head top at 10) into a 6 mm plate (z 4..10 shifted by top_z - 10) with a 90° sink."""
    plate = Pos(0, 0, top_z - 6) * Box(20, 20, 6, align=MIN_Z)
    plate -= Pos(0, 0, top_z - 6) * Cylinder(1.8, 6, align=MIN_Z)
    plate -= Pos(0, 0, top_z - 1.5) * Cone(1.5, 3.0, 1.5, align=MIN_Z)  # 90 degrees: radius 1.5 -> 3.0 over 1.5
    screw = pa.Screw("screw", "M3", "countersunk", 6.0, origin=(0, 0, 10), direction=(0, 0, 1))
    items = {"plate": stand_in("plate", plate), "screw": assemble_model.place_simple(screw)}
    return items, pa.Bolted("joint", "screw", [("plate", "clearance")], source="test")


def test_countersunk_seat_pass():
    items, bolt = countersunk_joint(10.0)
    result = ac.check_interface(bolt, items)
    assert result["status"] == "pass", result["message"]
    assert result["measured"]["head_overlap_mm3"] == {"plate": 0.0}
    assert result["measured"]["head_flush"] == pytest.approx(0.0, abs=0.01)


def test_countersunk_proud_fails():
    items, bolt = countersunk_joint(9.5)  # the sink is 0.5 mm lower than the head: the head stands proud
    result = ac.check_interface(bolt, items)
    assert result["status"] == "fail"
    assert "countersunk head" in result["message"]
    assert result["measured"]["head_flush"] == pytest.approx(-0.5, abs=0.01)


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
