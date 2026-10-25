"""Tool stand-ins and the access check in render_tools."""
import pytest
from build123d import Box, Location

import assemble_model
import hardware
import print3d_assembly as pa
import render_tools


def _items(*decls):
    return {d.name: assemble_model.place_simple(d) for d in decls}


def _plate_and_screw(**screw_kw):
    plate = pa.StandIn("plate", Box(40, 40, 4), at=Location((0, 0, -2)))
    screw = pa.Screw("s1", "M3", "socket", 8, (0, 0, 0), (0, 0, 1), **screw_kw)
    return plate, screw


def test_hex_sizes_per_head():
    assert render_tools.HEX_KEY == {
        "socket": {"M2": 1.5, "M2.5": 2.0, "M3": 2.5, "M4": 3.0, "M5": 4.0},
        "button": {"M3": 2.0, "M4": 2.5, "M5": 3.0},
        "countersunk": {"M3": 2.0, "M4": 2.5, "M5": 3.0},
    }
    for kind, sizes in hardware.HEADS.items():
        for size in sizes:
            assert size in render_tools.HEX_KEY[kind]


def test_tool_solid_dimensions():
    box = render_tools.tool_solid("M3", "socket", 12).bounding_box()
    assert box.min.Z == pytest.approx(3.01, abs=0.02)
    assert box.max.Z == pytest.approx(3 + 75 + 90, abs=0.02)
    assert box.size.X == pytest.approx(25, abs=0.02)
    assert box.size.Y == pytest.approx(25, abs=0.02)


def test_tool_name():
    assert render_tools.tool_name("s1") == "tool:s1"


def test_clear_screw():
    items = _items(*_plate_and_screw())
    tools = render_tools.place_tools(items, ["s1"])
    assert tools["tool:s1"].kind == "tool"
    assert tools["tool:s1"].color is None
    entry = render_tools.tool_access(items, tools)[0]
    assert entry == {"screw": "s1", "tool": "hex 2.5 mm", "status": "clear", "hits": [], "location": None}


def test_blocked_screw_names_hit_item():
    roof = pa.StandIn("roof", Box(40, 40, 2), at=Location((0, 0, 3 + 30)))
    items = _items(*_plate_and_screw(), roof)
    tools = render_tools.place_tools(items, ["s1"])
    entry = render_tools.tool_access(items, tools)[0]
    assert entry["status"] == "blocked"
    assert entry["hits"] == ["roof"]
    assert entry["location"][2] == pytest.approx(33, abs=1.5)


def test_own_washer_and_screw_ignored():
    plate, screw = _plate_and_screw()
    washer = pa.Washer("w1", "M3", (0, 0, 0), (0, 0, 1))
    items = _items(plate, washer, screw)
    tools = render_tools.place_tools(items, ["s1"])
    entry = render_tools.tool_access(items, tools)[0]
    assert entry["status"] == "clear"
    assert entry["hits"] == []


def test_tool_false_skipped():
    items = _items(*_plate_and_screw(tool=False))
    tools = render_tools.place_tools(items, ["s1"])
    assert tools == {}
    assert render_tools.tool_access(items, tools) == []
