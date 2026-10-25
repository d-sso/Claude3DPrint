"""Render scenes in scene_model: groups for models, units and layouts, explode offsets, looks, tools, staging."""
import json
import shutil

import pytest
from build123d import Box, Location
from OCP.gp import gp_Trsf

import assemble_model
import print3d_assembly as pa
import scene_model as sm
from assembly_sheets import FAIL_RGB, HARDWARE_RGB, PALETTE
from conftest import FIXTURES
from run_model import RunError

BOXLID = FIXTURES / "assembly" / "boxlid"
ROW = FIXTURES / "assembly" / "row"


@pytest.fixture(autouse=True)
def no_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINT3D_CONFIG", str(tmp_path / "no-config.toml"))


def to_location(matrix):
    trsf = gp_Trsf()
    trsf.SetValues(*matrix[0], *matrix[1], *matrix[2])
    return Location(trsf)


def obj(group, name):
    return next(o for o in group.objects if o.name == name)


def approx3(a, b, abs=1e-6):
    return all(x == pytest.approx(y, abs=abs) for x, y in zip(a, b))


@pytest.fixture(scope="module")
def boxlid():
    module, params, shared = assemble_model.load_assembly(BOXLID / "assembly.py", ())
    assembly = assemble_model.declare_assembly(module, params, shared)
    items = assemble_model.build_items(assembly, BOXLID, shared, {"warnings": []})
    unit = assembly.units[0]
    return unit, items[unit.name]


@pytest.fixture(scope="module")
def box_group(boxlid):
    return sm.unit_group(*boxlid)


def _place(*decls):
    return {d.name: assemble_model.place_simple(d) for d in decls}


def _stack(**explode):
    """Three 20 x 20 x 4 boxes with 6 mm gaps (top "a" 20..24, middle "c" 10..14, bottom "b" 0..4), a screw
    seated on the top box with a washer, and a nut under it."""
    def box(name, z):
        return pa.StandIn(name, Box(20, 20, 4), at=Location((0, 0, z)), explode=explode.get(name))

    return _place(
        box("a", 22), box("b", 2), box("c", 12),
        pa.Screw("s", "M3", "socket", 6, (0, 0, 24), (0, 0, 1), explode=explode.get("s")),
        pa.Washer("w", "M3", (0, 0, 24), (0, 0, 1)),
        pa.Nut("n", "M3", "hex", (0, 0, 20), (0, 0, 1)),
    )


# --- units --------------------------------------------------------------------------------------------------------


def test_flipped_part_matrix_matches_placement(boxlid, box_group):
    _, items = boxlid
    for name in ("lid", "base", "battery", "screw-left", "nut-right"):
        o = obj(box_group, name)
        assert len(o.matrix) == 4 and all(len(row) == 4 for row in o.matrix)
        moved = box_group.meshes[o.mesh].moved(to_location(o.matrix)).bounding_box()
        placed = items[name].shape.bounding_box()
        assert approx3(moved.min, placed.min) and approx3(moved.max, placed.max), name


def test_unit_group_looks_and_defaults(box_group):
    g = box_group
    assert (g.kind, g.name, g.folder) == ("unit", "box", "unit-box")
    lid = obj(g, "lid")
    assert lid.mesh == "unit/box/lid" and lid.kind == "part" and lid.collection == "items"
    assert lid.look == sm.Look("matte", (90, 140, 220), None)
    assert obj(g, "battery").look.material == "matte"
    for name in ("screw-left", "screw-right", "nut-left"):
        assert obj(g, name).look == sm.Look("metal", HARDWARE_RGB, None)
    assert g.render == pa.Render()
    assert approx3(g.bbox[0][2:], (0,), 1e-3) and g.bbox[1][2] > 23  # the screw heads stand above the lid
    tools = [o for o in g.objects if o.collection == "tools"]
    assert [o.name for o in tools] == ["tool:screw-left", "tool:screw-right"]
    assert tools[0].mesh == "tool/socket/M3/12" and tools[0].kind == "tool"
    assert tools[0].matrix == obj(g, "screw-left").matrix
    assert tools[0].explode == obj(g, "screw-left").explode
    assert [e["screw"] for e in g.tool_access] == ["screw-left", "screw-right"]


def test_unit_group_explodes_lid_above_base(box_group):
    step = sm.explode_step(box_group.bbox)
    assert obj(box_group, "base").explode == (0.0, 0.0, 0.0)  # by bbox bottom: base, battery (on pads), lid
    assert approx3(obj(box_group, "battery").explode, (0, 0, step))
    assert approx3(obj(box_group, "lid").explode, (0, 0, 2 * step))


def test_explode_step():
    assert sm.EXPLODE_GAP == 10.0
    assert sm.explode_step(((0, 0, 0), (1, 1, 4))) == 10.0
    assert sm.explode_step(((0, 0, -10), (1, 1, 40))) == 25.0


def test_default_explode_stacks_upwards():
    offsets = sm.default_explode(_stack(), (0, 0, 1), 7.0)
    assert approx3(offsets["b"], (0, 0, 0))
    assert approx3(offsets["c"], (0, 0, 7))
    assert approx3(offsets["a"], (0, 0, 14))
    assert approx3(offsets["s"], (0, 0, 14 + 6 + 10))
    assert approx3(offsets["w"], (0, 0, 14 + 5))
    assert approx3(offsets["n"], (0, 0, 14 - 10))


def test_default_explode_along_facing():
    offsets = sm.default_explode(_stack(), (1, 0, 0), 7.0)  # all three boxes start at the same x: one level
    assert all(approx3(offsets[n], (0, 0, 0)) for n in "abc")


def test_default_explode_contents_rise_out_of_their_container():
    # a 4 mm block resting 2 mm up inside a 20 mm tall box: its bottom is higher, though its centre is lower
    items = _place(pa.StandIn("box", Box(30, 30, 20), at=Location((0, 0, 10))),
                   pa.StandIn("block", Box(10, 10, 4), at=Location((0, 0, 4))))
    offsets = sm.default_explode(items, (0, 0, 1), 7.0)
    assert approx3(offsets["box"], (0, 0, 0)) and approx3(offsets["block"], (0, 0, 7))


def test_default_explode_same_height_items_share_a_rank():
    items = _place(pa.StandIn("a", Box(10, 10, 4), at=Location((0, 0, 2))),
                   pa.StandIn("b", Box(10, 10, 4), at=Location((20, 0, 2))),
                   pa.StandIn("c", Box(10, 10, 4), at=Location((40, 0, 2))),
                   pa.StandIn("top", Box(50, 10, 4), at=Location((20, 0, 6))))
    offsets = sm.default_explode(items, (0, 0, 1), 7.0)
    for name in "abc":
        assert approx3(offsets[name], (0, 0, 0))
    assert approx3(offsets["top"], (0, 0, 7))


def test_default_explode_levels_are_dense_and_tolerant():
    items = _place(pa.StandIn("a", Box(10, 10, 4), at=Location((0, 0, 2))),
                   pa.StandIn("b", Box(10, 10, 4), at=Location((20, 0, 2.005))),
                   pa.StandIn("tall", Box(10, 10, 20), at=Location((40, 0, 10))),
                   pa.StandIn("hi", Box(10, 10, 4), at=Location((60, 0, 30))))
    offsets = sm.default_explode(items, (0, 0, 1), 7.0)
    assert approx3(offsets["a"], (0, 0, 0)) and approx3(offsets["b"], (0, 0, 0))
    assert approx3(offsets["tall"], (0, 0, 0)) and approx3(offsets["hi"], (0, 0, 7))


def test_declared_explode_wins():
    offsets = sm.default_explode(_stack(a=(5, 0, 0), s=(0, 0, 1)), (0, 0, 1), 7.0)
    assert approx3(offsets["a"], (5, 0, 0))
    assert approx3(offsets["c"], (0, 0, 7))
    assert approx3(offsets["s"], (0, 0, 1))
    assert approx3(offsets["n"], (5, 0, -10))  # follows the nearest box's (declared) offset


def test_resolve_look_defaults_and_override():
    items = _place(pa.StandIn("lamp", Box(1, 1, 1), material="emissive"),
                   pa.StandIn("glow5", Box(1, 1, 1), material="emissive", glow=5),
                   pa.Washer("w", "M3", (0, 0, 0), (0, 0, 1), material="matte"))
    assert sm.resolve_look(items["lamp"], (1, 2, 3), None) == sm.Look("emissive", (1, 2, 3), pa.DEFAULT_GLOW)
    assert sm.resolve_look(items["glow5"], (1, 2, 3), None).glow == 5.0
    assert sm.resolve_look(items["glow5"], (1, 2, 3), {"glow": 20.0}).glow == 20.0
    assert sm.resolve_look(items["glow5"], (1, 2, 3), {"material": "matte"}) == sm.Look("matte", (1, 2, 3), None)
    assert sm.resolve_look(items["w"], (1, 2, 3), {"color": (9, 9, 9)}) == sm.Look("matte", (9, 9, 9), None)


def test_blocked_tool_is_red():
    unit = pa.Unit("u", [
        pa.StandIn("plate", Box(40, 40, 4), at=Location((0, 0, -2))),
        pa.Screw("open", "M3", "socket", 8, (10, 0, 0), (0, 0, 1)),
        pa.Screw("shut", "M3", "socket", 8, (-10, 0, 0), (0, 0, 1)),
        pa.StandIn("roof", Box(10, 10, 2), at=Location((-10, 0, 33))),
        pa.Screw("skip", "M3", "socket", 8, (0, 10, 0), (0, 0, 1), tool=False),
    ])
    items = {d.name: assemble_model.place_simple(d) for d in unit.items}
    g = sm.unit_group(unit, items)
    assert obj(g, "tool:shut").look == sm.Look("matte", FAIL_RGB, None)
    assert obj(g, "tool:open").look == sm.Look("matte", sm.TOOL_RGB, None)
    assert sm.TOOL_RGB == (60, 60, 70)
    assert "tool:skip" not in [o.name for o in g.objects]
    assert [(e["screw"], e["status"], e["hits"]) for e in g.tool_access] == [
        ("open", "clear", []), ("shut", "blocked", ["roof"])]
    assert any("shut" in w and "roof" in w for w in g.warnings)


# --- layouts ------------------------------------------------------------------------------------------------------


def _cell_layout(looks=None, items=()):
    cell = pa.Unit("cell", [
        pa.StandIn("lamp", Box(10, 10, 2), material="emissive", glow=5),
        pa.StandIn("block", Box(10, 10, 4), at=Location((0, 0, 3))),
        pa.Screw("bolt", "M3", "socket", 6, (0, 0, 5), (0, 0, 1)),
    ])
    layout = pa.Layout("row", [("c1", "cell", pa.place()), ("c2", "cell", pa.place(x=20))],
                       items=list(items), looks=looks)
    assembly = pa.Assembly(units=[cell], layouts=[layout])
    unit_items = {"cell": {d.name: assemble_model.place_simple(d) for d in cell.items}}
    return assembly.layouts[0], unit_items, assembly.units


def test_layout_glow_only_look_on_emissive_item():
    layout, unit_items, units = _cell_layout({"c1": {"lamp": {"glow": 20}}})
    g = sm.layout_group(layout, unit_items, units, None)
    assert obj(g, "c1.lamp").look == sm.Look("emissive", PALETTE[0], 20.0)
    assert obj(g, "c2.lamp").look.glow == 5.0


def test_layout_glow_only_look_on_matte_item_raises():
    layout, unit_items, units = _cell_layout({"c2": {"block": {"glow": 20}}})
    with pytest.raises(RunError, match=r"row.*c2.*block") as info:
        sm.layout_group(layout, unit_items, units, None)
    assert info.value.stage == "load"
    with pytest.raises(ValueError, match="glow"):
        _cell_layout({"c2": {"block": {"glow": 0}}})


def test_layout_tools_only_layout_screws():
    # the layout screw sits under c2's block: its driver hits the instance geometry
    rail = pa.StandIn("rail", Box(40, 4, 2), at=Location((10, 20, 1)))
    fix = pa.Screw("fix", "M3", "socket", 6, (20, 0, -10), (0, 0, 1))
    layout, unit_items, units = _cell_layout(items=[rail, fix])
    g = sm.layout_group(layout, unit_items, units, None)
    tools = [o for o in g.objects if o.kind == "tool"]
    assert [o.name for o in tools] == ["tool:fix"] and tools[0].collection == "tools"
    assert [e["screw"] for e in g.tool_access] == ["fix"]
    assert g.tool_access[0]["status"] == "blocked" and "c2.block" in g.tool_access[0]["hits"]
    assert obj(g, "c2.block").explode == (0.0, 0.0, 0.0)
    assert obj(g, "fix").mesh == "layout/row/fix" and obj(g, "rail").mesh == "layout/row/rail"
    assert approx3(obj(g, "c2.block").matrix[0][3:], (20,)) and obj(g, "c2.block").mesh == "unit/cell/block"


def test_layout_reuses_unit_meshes():
    groups, meta = sm.load_groups(ROW / "assembly.py", (), "layout", None, None, None)
    (g,) = groups
    assert (g.kind, g.name, g.folder) == ("layout", "row", "layout-row")
    blocks = [o for o in g.objects if o.name.endswith(".block")]
    assert [o.name for o in blocks] == ["c1.block", "c2.block", "c3.block", "c4.block"]
    assert {o.mesh for o in blocks} == {"unit/cell/block"}
    assert set(g.meshes) == {"unit/cell/block", "layout/row/strip"}
    assert approx3([o.matrix[0][3] for o in blocks], (0, 30, 60, 90))
    assert approx3(g.bbox[0][:1], (-15,), 1e-3) and approx3(g.bbox[1][:1], (105,), 1e-3)  # the strip
    assert set(meta) >= {"parameters", "overrides", "shared", "warnings"} and meta["shared"]["pitch"] == 30.0


def test_layout_variant_and_look_on_same_instance(tmp_path):
    root = tmp_path / "row"
    shutil.copytree(ROW, root, ignore=shutil.ignore_patterns("__pycache__", "out"))
    text = (root / "assembly.py").read_text(encoding="utf-8")
    text = text.replace('exact=("c1", "c2"),', 'exact=("c1", "c2"),\n        overrides={"c2": {"block": {"slot_dz": 1.0}}},'
                        '\n        looks={"c2": {"block": {"color": "#ff0000", "material": "emissive"}}},')
    (root / "assembly.py").write_text(text, encoding="utf-8")
    (g,) = sm.load_groups(root / "assembly.py", (), "layout", None, None, None)[0]
    c1, c2 = obj(g, "c1.block"), obj(g, "c2.block")
    assert c2.mesh == "layout/row/c2/block"
    assert c2.look == sm.Look("emissive", (255, 0, 0), 10.0)
    assert c1.mesh == "unit/cell/block" and c1.look == sm.Look("matte", (200, 200, 200), None)
    assert obj(g, "c3.block").mesh == "unit/cell/block"
    assert approx3(c2.matrix[0][3:], (30,))
    moved = g.meshes[c2.mesh].moved(to_location(c2.matrix)).bounding_box()
    assert approx3(moved.min, (15.2, -10, 0), 1e-3)


# --- models and loading -------------------------------------------------------------------------------------------


def _model(tmp_path, render):
    path = tmp_path / "plate.py"
    path.write_text(
        "from build123d import Box\n"
        "PARAMETERS = {'w': 10.0}\n"
        f"RENDER = {render!r}\n"
        "def build(p):\n    return Box(p['w'], 5, 2)\n",
        encoding="utf-8",
    )
    return path


def test_model_group_render_dict(tmp_path):
    path = _model(tmp_path, {"color": "#00ff00", "backdrop": "wall", "facing": (0, -1, 0)})
    g = sm.model_group(path, ("w=20",))
    assert (g.kind, g.name, g.folder) == ("model", "plate", "plate")
    assert g.render.backdrop == "wall" and g.render.facing == (0.0, -1.0, 0.0)
    (o,) = g.objects
    assert (o.name, o.mesh, o.kind, o.collection) == ("main", "model/main", "part", "items")
    assert o.look == sm.Look("matte", (0, 255, 0), None) and o.explode == (0.0, 0.0, 0.0)
    assert approx3(g.bbox[1], (10, 2.5, 1)) and g.tool_access == []
    with pytest.raises(RunError, match=r"shine.*backdrop") as info:
        sm.model_group(_model(tmp_path, {"shine": 1}), ())
    assert info.value.stage == "load"
    with pytest.raises(RunError, match="glow") as info:
        sm.model_group(_model(tmp_path, {"glow": 3}), ())
    assert info.value.stage == "load"
    with pytest.raises(RunError, match="facing"):
        sm.model_group(_model(tmp_path, {"backdrop": "wall"}), ())
    assert sm.model_group(_model(tmp_path, {"material": "emissive"}), ()).objects[0].look.glow == pa.DEFAULT_GLOW


def test_model_group_without_render_uses_palette():
    g = sm.model_group(FIXTURES / "multi_part.py", ())
    assert [(o.name, o.mesh, o.look) for o in g.objects] == [
        ("base", "model/base", sm.Look("matte", PALETTE[0], None)),
        ("lid", "model/lid", sm.Look("matte", PALETTE[1], None)),
    ]
    assert g.render == pa.Render()


def test_load_groups_selection():
    groups, meta = sm.load_groups(ROW / "assembly.py", (), "all", None, None, None)
    assert [g.folder for g in groups] == ["unit-cell", "layout-row"]
    groups, _ = sm.load_groups(ROW / "assembly.py", (), "all", "cell", None, None)
    assert [g.folder for g in groups] == ["unit-cell"]
    with pytest.raises(RunError) as info:
        sm.load_groups(ROW / "assembly.py", (), "all", "nope", None, None)
    assert info.value.stage == "usage"
    groups, meta = sm.load_groups(BOXLID / "assembly.py", ("screw_len=14",), "all", None, None, None)
    assert [g.folder for g in groups] == ["unit-box"]
    assert meta["parameters"] == {"screw_len": 14.0} and meta["overrides"] == {"screw_len": 14.0}
    groups, meta = sm.load_groups(FIXTURES / "good_box.py", ("height=20",), "all", None, None, None)
    assert [g.kind for g in groups] == ["model"] and meta["overrides"] == {"height": 20.0}


# --- staging ------------------------------------------------------------------------------------------------------


def test_write_staging_schema(box_group, tmp_path):
    path = sm.write_staging(box_group, tmp_path / "stage")
    assert path == tmp_path / "stage" / "scene.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"version", "group", "units", "bbox", "backdrop", "facing", "up", "meshes", "objects",
                         "tool_access"}
    assert data["version"] == 1 and data["group"] == {"kind": "unit", "name": "box"} and data["units"] == "mm"
    assert (data["backdrop"], data["facing"], data["up"]) == ("floor", None, [0, 0, 1])
    assert len(data["bbox"]) == 2 and all(len(p) == 3 for p in data["bbox"])
    assert list(data["meshes"]) == list(box_group.meshes)
    for rel in data["meshes"].values():
        stl = tmp_path / "stage" / rel
        assert stl.is_file() and stl.stat().st_size > 84
    assert data["meshes"]["unit/box/base"] == "meshes/0.stl"
    keys = {"name", "mesh", "matrix", "kind", "material", "rgb", "glow", "explode", "collection"}
    for o in data["objects"]:
        assert set(o) == keys and o["mesh"] in data["meshes"]
        assert len(o["matrix"]) == 4 and all(len(row) == 4 for row in o["matrix"])
    lid = next(o for o in data["objects"] if o["name"] == "lid")
    assert lid["rgb"] == [90, 140, 220] and lid["material"] == "matte" and lid["glow"] is None
    assert len(data["tool_access"]) == 2


def _assembly(tmp_path, body):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "assembly.py").write_text(body, encoding="utf-8")
    return root / "assembly.py"


def test_load_groups_warns_like_assemble(tmp_path):
    path = _assembly(tmp_path, "from print3d_assembly import Assembly\n"
                               "def build_assembly(a):\n    return Assembly([])\n")
    groups, meta = sm.load_groups(path, (), "all", None, None, None)
    assert groups == [] and "no units or layouts declared" in meta["warnings"]


def test_load_groups_activates_profile_before_loading(tmp_path):
    import print3d_profile

    path = _assembly(tmp_path, "from build123d import Box\n"
                               "from print3d_assembly import Assembly, StandIn, Unit\n"
                               "from print3d_profile import profile_value\n"
                               "BED = profile_value('printer.bed_x')\n"
                               "def build_assembly(a):\n"
                               "    return Assembly([Unit('u', [StandIn('s', Box(BED, 1, 1))])])\n")
    profile = print3d_profile.load_profile(overrides={"printer.bed_x": "100"})
    (g,), _ = sm.load_groups(path, (), "all", None, None, profile)
    assert approx3((g.bbox[1][0] - g.bbox[0][0],), (100,))
