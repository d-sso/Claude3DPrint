"""Render declaration fields in print3d_assembly: material, glow, explode, tool, Render, Layout.looks."""
import pytest
from build123d import Box

import assemble_model
import print3d_assembly as pa
from test_assembly_fasteners import BOXLID
from test_assembly_pattern import approx3


def test_item_material_defaults_none_and_validates():
    assert pa.Part("p", "p.py").material is None
    with pytest.raises(ValueError, match=r"material.*matte.*metal.*translucent.*emissive"):
        pa.Part("p", "p.py", material="glass")


def test_glow_requires_emissive():
    assert pa.StandIn("s", Box(1, 1, 1), material="emissive", glow=5).glow == 5.0
    with pytest.raises(ValueError, match="glow"):
        pa.StandIn("s", Box(1, 1, 1), material="matte", glow=5)
    with pytest.raises(ValueError, match="glow"):
        pa.StandIn("s", Box(1, 1, 1), material="emissive", glow=0)


def test_explode_is_free_vector():
    screw = pa.Screw("s", "M3", "socket", 10, (0, 0, 0), (0, 0, 1), explode=(0, 0, 12))
    assert screw.explode == (0.0, 0.0, 12.0)
    with pytest.raises(ValueError, match="explode"):
        pa.Screw("s", "M3", "socket", 10, (0, 0, 0), (0, 0, 1), explode=(0, 0))


def test_screw_tool_flag():
    assert pa.Screw("s", "M3", "socket", 10, (0, 0, 0), (0, 0, 1)).tool is True
    with pytest.raises(ValueError, match="tool"):
        pa.Screw("s", "M3", "socket", 10, (0, 0, 0), (0, 0, 1), tool="no")


def test_render_wall_needs_facing():
    with pytest.raises(ValueError, match="facing"):
        pa.Render("wall")
    assert pa.Render("wall", facing=(0, -2, 0)).facing == (0.0, -1.0, 0.0)
    with pytest.raises(ValueError, match="backdrop"):
        pa.Render("ceiling")


def cell_assembly(looks, exact=()):
    cell = pa.Unit("cell", [pa.StandIn("block", Box(1, 1, 1)), pa.Screw("bolt", "M3", "socket", 8, (0, 0, 0), (0, 0, 1))])
    layout = pa.Layout("row", [("c1", "cell", pa.place()), ("c2", "cell", pa.place(x=5))], looks=looks, exact=exact)
    return pa.Assembly(units=[cell], layouts=[layout])


def test_layout_looks_validated():
    with pytest.raises(ValueError, match="c1.*c2"):
        cell_assembly({"zz": {"block": {"color": "#ff0000"}}})
    with pytest.raises(ValueError, match="cell.*nope|nope.*cell"):
        cell_assembly({"c1": {"nope": {"color": "#ff0000"}}})
    with pytest.raises(ValueError, match="color.*material.*glow"):
        cell_assembly({"c1": {"block": {"shine": 1}}})
    a = cell_assembly({"c1": {"block": {"color": "#ff0000"}}})
    assert a.layouts[0].looks["c1"]["block"]["color"] == (255, 0, 0)
    cell_assembly({"c1": {"bolt": {"material": "metal"}}})


def test_looks_need_not_be_exact():
    cell_assembly({"c2": {"block": {"material": "matte"}}}, exact=("c1",))


def test_repeat_turns_explode_offset():
    copies = pa.repeat(pa.Part("p", "p.py", explode=(10, 0, 0)), pa.polar(4))
    assert approx3(copies[1].explode, (0, 10, 0))
    assert pa.repeat(pa.Part("p", "p.py"), pa.polar(2))[1].explode is None


def test_existing_positional_calls_unchanged():
    module, params, shared = assemble_model.load_assembly(BOXLID / "assembly.py", ())
    assert module.build_assembly({"screw_len": 12.0}) is not None
    assert pa.Nut("n", "M3", "hex", (0, 0, 0), (0, 0, 1), None, 30).angle == 30.0
