"""Loading an assembly.py, building its parts and placing them (print3d_assembly + assemble_model)."""
import shutil

import pytest

import assemble_model
import print3d_assembly as pa
import run_model
from assembly_geometry import hole_on_axis
from conftest import FIXTURES

BOXLID = FIXTURES / "assembly" / "boxlid"
ASSEMBLY = BOXLID / "assembly.py"


def build(path=ASSEMBLY, set_pairs=()):
    module, params, shared_overrides = assemble_model.load_assembly(path, set_pairs)
    assembly = assemble_model.declare_assembly(module, params, shared_overrides)
    result = {"warnings": []}
    items = assemble_model.build_items(assembly, path.parent, shared_overrides, result)
    return assembly, items, result


def test_load_boxlid():
    module, params, shared_overrides = assemble_model.load_assembly(ASSEMBLY)
    assert params == {"screw_len": 12.0}
    assert shared_overrides == {}
    assembly, items, result = build()
    assert [u.name for u in assembly.units] == ["box"]
    box = items["box"]
    assert sorted(box) == ["base", "battery", "lid", "nut-left", "nut-right", "screw-left", "screw-right"]
    assert {name: item.kind for name, item in box.items()} == {
        "base": "part", "lid": "part", "battery": "standin",
        "screw-left": "screw", "screw-right": "screw", "nut-left": "nut", "nut-right": "nut",
    }
    assert result["warnings"] == []
    lid = box["lid"].shape.bounding_box()
    assert (round(lid.min.Z, 3), round(lid.max.Z, 3)) == (16.0, 23.0)
    screw = box["screw-left"].shape.bounding_box()
    assert (round(screw.min.Z, 3), round(screw.max.Z, 3)) == (11.0, 26.0)  # 12 under the head, 3 mm head
    nut = box["nut-right"].shape.bounding_box()
    assert (round(nut.min.Z, 3), round(nut.max.Z, 3)) == (11.9, 14.3)
    origin, direction = box["screw-left"].axis()
    assert origin == pytest.approx((-14.0, 0.0, 23.0))
    assert direction == pytest.approx((0.0, 0.0, 1.0))
    battery = box["battery"].shape.bounding_box()
    assert [round(v, 3) for v in (battery.size.X, battery.size.Y, battery.size.Z)] == [10.0, 20.0, 4.0]


def test_unknown_set_key_lists_valid():
    with pytest.raises(run_model.RunError) as info:
        assemble_model.load_assembly(ASSEMBLY, ["thickness=3"])
    assert info.value.stage == "params"
    for name in ("thickness", "box_x", "screw_len"):
        assert name in info.value.message


def test_assembly_param_set():
    _, params, shared_overrides = assemble_model.load_assembly(ASSEMBLY, ["screw_len=6"])
    assert params == {"screw_len": 6.0}
    assert shared_overrides == {}


def test_shared_set_changes_every_part():
    _, params, shared_overrides = assemble_model.load_assembly(ASSEMBLY, ["wall=2.4"])
    assert shared_overrides == {"wall": 2.4}
    _, items, _ = build(set_pairs=["wall=2.4"])
    box = items["box"]
    assert box["base"].shared["wall"] == 2.4
    assert box["lid"].shared["wall"] == 2.4
    # the lid's skirt follows the thicker wall: 40 - 2 * 2.4 - 2 * 0.15 = 34.9 wide
    skirt = box["lid"].shape.bounding_box()
    assert round(skirt.size.X, 3) == 40.0
    section = [f for f in box["lid"].shape.faces() if abs(f.center().Z - 16.0) < 1e-6]
    width = max(f.bounding_box().max.X for f in section) - min(f.bounding_box().min.X for f in section)
    assert width == pytest.approx(34.9, abs=1e-3)


def _count_builds(monkeypatch):
    calls = []
    real = run_model.load_model

    def load(path, shared_overrides=None):
        module = real(path, shared_overrides)
        inner = module.build

        def counted(p):
            calls.append((module.__file__, dict(p)))
            return inner(p)

        module.build = counted
        return module

    monkeypatch.setattr(run_model, "load_model", load)
    return calls


def _lids(*params):
    items = [pa.Part(f"lid{i}", "parts/lid/lid.py", params=p) for i, p in enumerate(params)]
    return pa.Assembly([pa.Unit("u", items)])


def test_same_model_different_params_builds_twice(monkeypatch):
    calls = _count_builds(monkeypatch)
    items = assemble_model.build_items(_lids(None, {"holes": False}), BOXLID, {}, {"warnings": []})
    assert len(calls) == 2
    assert items["u"]["lid1"].shape.volume > items["u"]["lid0"].shape.volume  # no holes: more material
    assert items["u"]["lid0"].shape.volume != pytest.approx(items["u"]["lid1"].shape.volume)

    calls.clear()
    assemble_model.build_items(_lids({"holes": False}, {"holes": False}), BOXLID, {}, {"warnings": []})
    assert len(calls) == 1


def test_flipped_part_hole_found_on_unit_axis():
    _, items, _ = build()
    lid = items["box"]["lid"].shape
    # the lid skirt's inner face is 2.65 mm from the bolt axis: those depths are excluded, the plate is measured
    for x in (-14.0, 14.0):
        probe = hole_on_axis(lid, (x, 0, 20.0), (0, 0, -1), search_r=3)
        assert probe.found, probe.reason
        assert probe.offset <= 0.05
        assert probe.depth_span == pytest.approx((-3.0, 0.0), abs=1e-6)  # the plate, now at z = 20..23


def _copy(tmp_path):
    root = tmp_path / "boxlid"
    shutil.copytree(BOXLID, root, ignore=shutil.ignore_patterns("__pycache__", "out"))
    return root


def test_shared_name_not_from_shared_warns(tmp_path):
    root = _copy(tmp_path)
    lid = root / "parts" / "lid" / "lid.py"
    text = lid.read_text(encoding="utf-8")
    text = text.replace('S = shared("box_x", "box_y", "wall", "bolt_dx", "groove_z")',
                        'S = shared("box_x", "box_y", "bolt_dx", "groove_z")')
    text = text.replace('PARAMETERS = {\n', 'PARAMETERS = {\n    "wall": 2.0,\n')
    text = text.replace('bx, by, wall, dx = S["box_x"], S["box_y"], S["wall"], S["bolt_dx"]',
                        'bx, by, wall, dx = S["box_x"], S["box_y"], p["wall"], S["bolt_dx"]')
    lid.write_text(text, encoding="utf-8")
    _, _, result = build(root / "assembly.py")
    assert result["warnings"] == [
        "Part 'lid' (parts/lid/lid.py): parameter 'wall' has a shared name but does not come from shared(); "
        "assemble --set won't change it"
    ]


def test_part_build_error_names_part(tmp_path):
    root = _copy(tmp_path)
    lid = root / "parts" / "lid" / "lid.py"
    lines = lid.read_text(encoding="utf-8").splitlines()
    at = lines.index("def build(p):") + 1
    lines.insert(at, '    raise ValueError("lid is broken")')
    lid.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(run_model.RunError) as info:
        build(root / "assembly.py")
    err = info.value
    assert err.stage == "build"
    assert "lid" in err.message and "parts/lid/lid.py" in err.message
    assert "lid is broken" in err.message
    assert err.line == at + 1
    assert err.model == lid.resolve().as_posix()


def test_bad_declaration_field_is_clear():
    with pytest.raises(ValueError) as info:
        pa.Bolted("b", "screw", through=[("lid", "glue")], source="x")
    for word in ("glue", "clearance", "thread", "insert"):
        assert word in str(info.value)


def test_declaration_checks_references():
    base = pa.Part("base", "parts/base/base.py")
    with pytest.raises(ValueError, match="unknown item 'lidd'"):
        pa.Unit("u", [base], [pa.Clear("c", "lidd", "base", 1.0, source="x")])
    with pytest.raises(ValueError, match="must be a Screw"):
        pa.Unit("u", [base], [pa.Bolted("b", "base", [("base", "clearance")], source="x")])
    with pytest.raises(ValueError, match="source"):
        pa.Clear("c", "a", "b", 1.0, source="")


def test_multi_shape_part_needs_selection(tmp_path):
    root = _copy(tmp_path)
    (root / "parts" / "pair.py").write_text(
        "from build123d import Box\nPARAMETERS = {}\n\n\ndef build(p):\n"
        "    return {'a': Box(1, 1, 1), 'b': Box(2, 2, 2)}\n",
        encoding="utf-8",
    )
    unit = pa.Unit("u", [pa.Part("pair", "parts/pair.py")])
    with pytest.raises(run_model.RunError, match="several parts"):
        assemble_model.build_items(pa.Assembly([unit]), root, {}, {"warnings": []})
    unit = pa.Unit("u", [pa.Part("pair", "parts/pair.py", params={"_part": "b"})])
    items = assemble_model.build_items(pa.Assembly([unit]), root, {}, {"warnings": []})
    assert items["u"]["pair"].shape.volume == pytest.approx(8.0)


def test_place_rotation_order():
    loc = pa.place(10, 0, 0, rx=90, rz=90)
    # (0, 1, 0) -> rx 90 -> (0, 0, 1) -> rz 90 -> (0, 0, 1) -> +x 10
    from build123d import Location

    def moved(loc, point):
        return tuple((loc * Location(point)).position)

    assert moved(loc, (0, 1, 0)) == pytest.approx((10, 0, 1))
    assert moved(loc, (1, 0, 0)) == pytest.approx((10, 1, 0))
    assert moved(pa.flip(), (0, 1, 1)) == pytest.approx((0, -1, -1))


def test_place_layout_names_and_overrides():
    unit = pa.Unit("u", [pa.Part("lid", "parts/lid/lid.py"), pa.StandIn("s", __import__("build123d").Box(1, 1, 1))])
    layout = pa.Layout(
        "row", [("c1", "u", pa.place(0)), ("c2", "u", pa.place(50))], overrides={"c2": {"lid": {"holes": False}}}
    )
    assembly = pa.Assembly([unit], [layout])
    result = {"warnings": []}
    builder = assemble_model.Builder(BOXLID, {}, result)
    items = assemble_model.build_items(assembly, BOXLID, {}, result, builder=builder)
    placed = assemble_model.place_layout(layout, items, builder)
    assert sorted(placed) == ["c1.lid", "c1.s", "c2.lid", "c2.s"]
    assert placed["c2.s"].shape.center().X == pytest.approx(50.0)
    assert placed["c2.lid"].shape.volume > placed["c1.lid"].shape.volume
    assert placed["c2.lid"].decl.params == {"holes": False}
