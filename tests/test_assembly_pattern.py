"""Declaration patterns (polar, linear, grid, repeat) and one-sided targets (min/max) in print3d_assembly."""
import dataclasses
import json
import math

import pytest
from build123d import Box

import assemble_model
import assembly_checks as ac
import print3d_assembly as pa
from test_assembly_fasteners import BOXLID, load

SRC = "test"


def approx3(value, expected, abs=1e-6):
    return all(a == pytest.approx(b, abs=abs) for a, b in zip(value, expected))


@pytest.fixture(scope="module")
def boxlid():
    return load()


# --- locations ----------------------------------------------------------------------------------------------------


def test_polar_rotates_points_and_directions():
    locs = pa.polar(6)
    assert len(locs) == 6
    screws = pa.repeat(pa.Screw("s", "M3", "socket", 10, origin=(10, 0, 5), direction=(1, 0, 0)), locs)
    # 60 degrees about +Z: (10, 0, 5) -> (5, 8.66, 5); the direction rotates, but is not moved
    assert approx3(screws[1].origin, (5, 10 * math.sin(math.radians(60)), 5))
    assert approx3(screws[1].direction, (0.5, math.sin(math.radians(60)), 0))
    assert approx3(screws[3].origin, (-10, 0, 5))
    assert approx3(screws[0].origin, (10, 0, 5))


def test_polar_start_step_axis_and_center():
    locs = pa.polar(3, start_deg=90, step_deg=90, axis=(1, 0, 0), center=(0, 0, 10))
    nuts = pa.repeat(pa.Nut("n", "M3", "hex", origin=(0, 0, 20), direction=(0, 0, 1)), locs)
    assert approx3(nuts[0].origin, (0, -10, 10))  # 90 degrees about X through (0, 0, 10)
    assert approx3(nuts[0].direction, (0, -1, 0))
    assert approx3(nuts[1].origin, (0, 0, 0))
    assert approx3(nuts[2].origin, (0, 10, 10))


def test_linear_and_grid():
    locs = pa.linear(3, (5, 0, 1), start=(1, 2, 3))
    washers = pa.repeat(pa.Washer("w", "M3", origin=(0, 0, 0), direction=(0, 1, 0)), locs)
    assert [w.origin for w in washers] == [(1, 2, 3), (6, 2, 4), (11, 2, 5)]
    assert all(w.direction == (0, 1, 0) for w in washers)
    grid = pa.grid(2, 3, 10, 20)
    assert len(grid) == 6
    points = [tuple(round(v, 6) for v in loc.position) for loc in grid]
    assert points == [(0, 0, 0), (10, 0, 0), (0, 20, 0), (10, 20, 0), (0, 40, 0), (10, 40, 0)]


@pytest.mark.parametrize("call, message", [
    (lambda: pa.polar(0), "polar: n"),
    (lambda: pa.polar(2.5), "polar: n"),
    (lambda: pa.polar(4, axis=(0, 0, 0)), "polar: axis"),
    (lambda: pa.linear(3, (1, 0)), "linear: step"),
    (lambda: pa.grid(2, 0, 1, 1), "grid: ny"),
])
def test_location_helpers_reject_bad_arguments(call, message):
    with pytest.raises(ValueError, match=message):
        call()


# --- repeat: names and references ---------------------------------------------------------------------------------


def test_repeat_names():
    screw = pa.Screw("screw", "M3", "socket", 10, origin=(0, 0, 0), direction=(0, 0, 1))
    assert [s.name for s in pa.repeat(screw, pa.linear(3, (1, 0, 0)))] == ["screw-0", "screw-1", "screw-2"]
    assert [s.name for s in pa.repeat(screw, pa.linear(2, (1, 0, 0)), name="m3-{i}-{name}")] == [
        "m3-0-screw", "m3-1-screw"]
    templated = dataclasses.replace(screw, name="screw-{i}")
    assert [s.name for s in pa.repeat(templated, pa.linear(2, (1, 0, 0)))] == ["screw-0", "screw-1"]


def test_repeat_references_with_and_without_index():
    bolt = pa.Bolted("bolt-{i}", "screw-{i}", [("tray", "clearance"), ("frame", "thread")], nut="nut-{i}",
                     source=SRC, protrusion=1.0)
    bolts = pa.repeat(bolt, pa.polar(6))
    assert [b.name for b in bolts] == [f"bolt-{i}" for i in range(6)]
    assert [b.screw for b in bolts] == [f"screw-{i}" for i in range(6)]
    assert [b.nut for b in bolts] == [f"nut-{i}" for i in range(6)]
    assert all(b.through == (("tray", "clearance"), ("frame", "thread")) for b in bolts)
    assert all(b.source == SRC and b.protrusion == 1.0 for b in bolts)
    passage = pa.Passage("strip-{i}", "strip-{i}", ["c{i}.block", "frame"], 0.4, source=SRC)
    second = pa.repeat(passage, pa.linear(2, (0, 10, 0)))[1]
    assert second.item == "strip-1" and second.through == ("c1.block", "frame")
    size = pa.Size("height-{i}", ["base", "lid-{i}"], "z", 23.0, source=SRC)
    assert pa.repeat(size, pa.linear(2, (5, 0, 0)))[1].items == ("base", "lid-1")


def test_repeat_rejects_bad_templates():
    screw = pa.Screw("screw", "M3", "socket", 10, origin=(0, 0, 0), direction=(0, 0, 1))
    with pytest.raises(ValueError, match=r"repeat: name .*\{j\}.*\{name\} and \{i\}"):
        pa.repeat(screw, pa.linear(2, (1, 0, 0)), name="screw-{j}")
    with pytest.raises(ValueError, match=r"repeat: name 'screw-\{j\}'"):
        pa.repeat(dataclasses.replace(screw, name="screw-{j}"), pa.linear(2, (1, 0, 0)))
    bolt = pa.Bolted("bolt-{i}", "screw-{j}", [("tray", "clearance")], source=SRC)
    with pytest.raises(ValueError, match=r"repeat: Bolted 'bolt-\{i\}' screw .*\{j\}.*\{i\}"):
        pa.repeat(bolt, pa.linear(2, (1, 0, 0)))
    with pytest.raises(ValueError, match="repeat: template must be"):
        pa.repeat(pa.Unit("u", [screw]), pa.linear(2, (1, 0, 0)))
    with pytest.raises(ValueError, match="repeat: locations"):
        pa.repeat(screw, [(1, 0, 0)])


def test_repeat_result_is_validated():
    # a rotated Size axis must stay a principal axis
    size = pa.Size("w", ["base"], "x", 40.0, source=SRC)
    assert [s.axis for s in pa.repeat(size, pa.polar(2, step_deg=90))] == ["x", "y"]
    with pytest.raises(ValueError, match="repeat: Size 'w' axis"):
        pa.repeat(size, pa.polar(2, step_deg=45))
    # a duplicate name shows up when the list is used, like a hand-written duplicate
    screws = pa.repeat(pa.Screw("s", "M3", "socket", 10, origin=(0, 0, 0), direction=(0, 0, 1)),
                       pa.linear(2, (1, 0, 0)), name="{name}")
    with pytest.raises(ValueError, match="used twice"):
        pa.Unit("u", screws)


def test_repeat_parts_compose_at_and_transform_interfaces():
    part = pa.Part("tile", "parts/tile/tile.py", at=pa.place(z=3, rx=180))
    tiles = pa.repeat(part, pa.linear(2, (50, 0, 0)))
    assert approx3(tiles[1].at.position, (50, 0, 3))
    assert approx3(tiles[1].at.orientation, part.at.orientation)
    stand_in = pa.StandIn("strip", Box(1, 1, 1), at=pa.place(x=10))
    assert approx3(pa.repeat(stand_in, pa.polar(4))[1].at.position, (0, 10, 0))
    seat = pa.Seat("seat", "a", "b", point=(10, 0, 0), normal=(1, 0, 0), source=SRC)
    turned = pa.repeat(seat, pa.polar(4))[1]
    assert approx3(turned.point, (0, 10, 0)) and approx3(turned.normal, (0, 1, 0))
    step = pa.Step("flush", "a", "b", (10, 0, 0), (10, 0, 5), (-1, 0, 0), 0.0, source=SRC)
    turned = pa.repeat(step, pa.polar(4))[1]
    assert approx3(turned.origin_a, (0, 10, 0)) and approx3(turned.origin_b, (0, 10, 5))
    assert approx3(turned.direction, (0, -1, 0))
    gap = pa.Gap("g", "a", "b", None, 1.0, source=SRC)
    assert pa.repeat(gap, pa.polar(2))[1].direction is None


def test_repeated_snap_normals_rotate():
    snap = pa.Snap("snap-{i}", "lid", "base", point=(0, -11, 18), normal=(1, 0, 0), insert_dir=(0, 0, -1),
                   engagement=(0.3, 0.4), clearance=(0.1, 0.2), source=SRC)
    snaps = pa.repeat(snap, pa.polar(4))
    assert approx3(snaps[1].point, (11, 0, 18))
    assert approx3(snaps[1].normal, (0, 1, 0))
    assert approx3(snaps[1].insert_dir, (0, 0, -1))
    assert snaps[1].engagement == (0.3, 0.4)


def test_repeated_bolts_match_hand_written_bolts(boxlid):
    unit, items, _ = boxlid
    sides = pa.polar(2, start_deg=180)  # from x = -14: copy 0 at x = +14 (right), copy 1 at x = -14 (left)
    hardware = (pa.repeat(unit.item("screw-left"), sides, name="screw-{i}")
                + pa.repeat(unit.item("nut-left"), sides, name="nut-{i}"))
    assert approx3(hardware[0].origin, (14, 0, 23)) and approx3(hardware[1].origin, (-14, 0, 23))
    placed = {k: v for k, v in items.items() if not k.startswith(("screw-", "nut-"))}  # the hand-written ones
    placed.update({h.name: assemble_model.place_simple(h) for h in hardware})
    bolts = pa.repeat(pa.Bolted("bolt-{i}", "screw-{i}", [("lid", "clearance"), ("base", "clearance")],
                                nut="nut-{i}", source=SRC), sides)
    for bolt, side in zip(bolts, ("right", "left")):
        got = ac.check_interface(bolt, placed)
        want = ac.check_interface(next(x for x in unit.interfaces if x.name == f"bolt-{side}"), items)
        assert got["status"] == want["status"] == "pass", got["message"]
        assert got["measured"] == want["measured"]
        assert got["location"] == want["location"]


def test_repeated_bolt_pattern_in_a_unit(tmp_path):
    """The boxlid joints declared with repeat give the same results as the hand-written ones."""
    import shutil

    project = tmp_path / "boxlid"
    shutil.copytree(BOXLID, project, ignore=shutil.ignore_patterns("__pycache__", "out"))
    text = (project / "assembly.py").read_text(encoding="utf-8")
    hand = "    for side, sx in ((\"left\", -dx), (\"right\", dx)):"
    assert hand in text
    start = text.index(hand)
    end = text.index("    for side, sy in")
    patterned = (
        "    from print3d_assembly import polar, repeat\n"
        "    sides = polar(2, start_deg=180)  # copy 0 at x = +dx, copy 1 at x = -dx\n"
        "    items += repeat(Screw('screw', 'M3', 'socket', a['screw_len'], origin=(-dx, 0, top), direction=(0, 0, 1)),"
        " sides)\n"
        "    items += repeat(Nut('nut', 'M3', 'hex', origin=(-dx, 0, NUT_FAR), direction=(0, 0, 1)), sides)\n"
        "    interfaces += repeat(Bolted('bolt-{i}', 'screw-{i}', [('lid', 'clearance'), ('base', 'clearance')],"
        " nut='nut-{i}', source=SRC), sides)\n"
        "    interfaces += repeat(NutPocket('nut-{i}', 'nut-{i}', 'base', source=SRC), sides)\n"
    )
    (project / "assembly.py").write_text(text[:start] + patterned + text[end:], encoding="utf-8")
    pattern, _ = assemble_model.assemble(project / "assembly.py", out_dir=tmp_path / "out1", sheets=False)
    hand_written, _ = assemble_model.assemble(BOXLID / "assembly.py", out_dir=tmp_path / "out2", sheets=False)
    assert pattern["ok"], pattern["error"]

    def by_type(result, kind):
        return sorted((r["status"], json.dumps(r["measured"], sort_keys=True))
                      for r in result["units"][0]["interfaces"] if r["type"] == kind)

    for kind in ("bolted", "nut_pocket"):
        assert by_type(pattern, kind) == by_type(hand_written, kind)
        assert len(by_type(pattern, kind)) == 2
    assert pattern["status"] == hand_written["status"] == "pass"


# --- one-sided targets --------------------------------------------------------------------------------------------


def battery_gap(**bounds):
    return pa.Gap("battery-gap", "battery", "base", None, None, source="fixture.md § Battery", **bounds)


def test_min_only_target(boxlid):
    _, items, _ = boxlid  # the battery sits 2.0 above the floor
    result = ac.check_target(battery_gap(min=1.5), items)
    assert result["status"] == "pass", result["message"]
    assert result["expected"] == {"min": 1.5, "max": None}
    assert result["value"] is None
    json.dumps(result)
    result = ac.check_target(battery_gap(min=2.5), items)
    assert result["status"] == "fail"
    assert "gap 2 mm is below the minimum 2.5" in result["message"]
    assert "fixture.md § Battery" in result["message"]
    assert result["measured"] == pytest.approx(2.0, abs=0.01)


def test_max_only_target(boxlid):
    _, items, _ = boxlid
    assert ac.check_target(battery_gap(max=2.5), items)["status"] == "pass"
    result = ac.check_target(battery_gap(max=1.0), items)
    assert result["status"] == "fail"
    assert "gap 2 mm is above the maximum 1" in result["message"]
    assert result["expected"] == {"min": None, "max": 1.0}


def test_min_and_max_target(boxlid):
    unit, items, _ = boxlid
    height = next(t for t in unit.targets if t.name == "height")
    bounded = dataclasses.replace(height, value=None, min=22.5, max=23.5)
    result = ac.check_target(bounded, items)
    assert result["status"] == "pass", result["message"]
    assert result["expected"] == {"min": 22.5, "max": 23.5}
    assert result["tol"] is None and result["value"] is None
    assert "22.5" in result["message"] and "23.5" in result["message"]
    result = ac.check_target(dataclasses.replace(bounded, min=23.5, max=24.0), items)
    assert result["status"] == "fail"
    assert "size 23 mm is below the minimum 23.5" in result["message"]


def test_one_sided_target_declarations():
    with pytest.raises(ValueError, match=r"Gap 'battery-gap': .*value.*min.*max"):
        pa.Gap("battery-gap", "battery", "base", None, 2.0, min=1.0, source=SRC)
    with pytest.raises(ValueError, match=r"Size 'h': min must be at most max"):
        pa.Size("h", ["base"], "z", None, min=3.0, max=2.0, source=SRC)
    with pytest.raises(ValueError, match=r"Position 'p': max must be a finite number"):
        pa.Position("p", "lid", (0, 0, 0), (1, 0, 0), None, max="far", source=SRC)
    step = pa.Step("s", "a", "b", (0, 0, 0), (0, 0, 1), (1, 0, 0), None, max=0.2, source=SRC)
    assert step.max == 0.2 and step.min is None and step.value is None


def test_no_target_still_fails(boxlid):
    _, items, _ = boxlid
    result = ac.check_target(battery_gap(), items)
    assert result["status"] == "fail"
    assert result["message"].startswith("no target:")


def test_repeat_turns_nut_corners_with_the_copy():
    # a nut about +Z, copied round +Z: its angle follows the rotation, so its corners stay aimed the same way
    nuts = pa.repeat(pa.Nut("n", "M3", "hex", origin=(10, 0, 0), direction=(0, 0, 1), angle=10), pa.polar(4))
    assert [round(n.angle % 360, 6) for n in nuts] == [10, 100, 190, 280]
    # a nut along +X turned 90 degrees about +Z: its direction becomes +Y, and its corner (at angle 0 along +Z) stays
    # along +Z, which is also the zero reference for +Y
    turned = pa.repeat(pa.Nut("n", "M3", "hex", origin=(0, 0, 0), direction=(1, 0, 0)), [pa.place(rz=90)])[0]
    assert approx3(turned.direction, (0, 1, 0))
    assert turned.angle == pytest.approx(0.0, abs=1e-9)
