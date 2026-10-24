"""Straight hex-driver stand-ins on each screw's axis, and the check that the driver can reach the screw."""
from __future__ import annotations

import math

import numpy as np
from build123d import Face, Plane, Solid, Vector, Wire

import assembly_geometry
import hardware

HEX_KEY = {  # mm across flats (ISO 4762 / 7380 / 10642)
    "socket": {"M2": 1.5, "M2.5": 2.0, "M3": 2.5, "M4": 3.0, "M5": 4.0},
    "button": {"M3": 2.0, "M4": 2.5, "M5": 3.0},
    "countersunk": {"M3": 2.0, "M4": 2.5, "M5": 3.0},
}
TIP_LEN = 10.0  # mm, the hex tip
SHAFT_LEN = 75.0  # mm from the head top, the tip included
HANDLE_D = 25.0  # mm
HANDLE_LEN = 90.0  # mm
TIP_GAP = 0.01  # mm, the tip starts this far above the head top


def shaft_d(hex_size):
    return max(hex_size + 1.5, 4.0)


def tool_name(screw_name):
    return f"tool:{screw_name}"


def _cyl(radius, height, z):
    return Solid.make_cylinder(radius, height, Plane(origin=(0, 0, z), z_dir=(0, 0, 1)))


def tool_solid(size, kind, length):
    """The driver in the screw's local frame (+Z out of the head): hex tip, round shaft, handle."""
    hex_size = HEX_KEY[kind][size]
    top = hardware.fastener(size, kind, length).head_top
    z0 = top + TIP_GAP
    r = hex_size / math.sqrt(3)  # corner radius
    pts = [Vector(r * math.cos(math.radians(60 * i)), r * math.sin(math.radians(60 * i)), z0) for i in range(6)]
    tip = Solid.extrude(Face(Wire.make_polygon(pts, close=True)), Vector(0, 0, TIP_LEN))
    shaft_start = z0 + TIP_LEN
    shaft = _cyl(shaft_d(hex_size) / 2, top + SHAFT_LEN - shaft_start, shaft_start)
    handle = _cyl(HANDLE_D / 2, HANDLE_LEN, top + SHAFT_LEN)
    return tip.fuse(shaft).fuse(handle).clean()


def place_tools(items, screws):
    """A tool PlacedItem for each named screw (skipping those with tool=False): {tool name: PlacedItem}."""
    from assemble_model import PlacedItem

    tools = {}
    for name in screws:
        screw = items[name]
        decl = screw.decl
        if decl.tool is False:
            continue
        shape = tool_solid(decl.size, decl.kind, decl.length).moved(screw.loc)
        tools[tool_name(name)] = PlacedItem(tool_name(name), "tool", shape, None, None, {}, decl, screw.loc)
    return tools


def _on_axis(item, origin, direction):
    offset = np.array(item.axis()[0]) - np.array(origin)
    d = np.array(direction)
    return np.linalg.norm(offset - np.dot(offset, d) * d) <= assembly_geometry.AXIS_LIMIT


def tool_access(items, tools):
    """One entry per tool, in screw-name order: whether its driver clears every other placed item."""
    entries = []
    for tool in sorted(tools.values(), key=lambda t: t.decl.name):
        screw = tool.decl.name
        origin, direction = items[screw].axis()
        hits, location = [], None
        for name, item in items.items():
            if name == screw or item.kind == "tool":
                continue
            if item.kind == "washer" and _on_axis(item, origin, direction):
                continue
            if not assembly_geometry._boxes_meet(tool.shape, item.shape):
                continue
            volume, centre = assembly_geometry.overlap(tool.shape, item.shape)
            if volume > assembly_geometry.OVERLAP_EPS:
                hits.append(name)
                if location is None:
                    location = [float(v) for v in centre]
        hex_size = HEX_KEY[tool.decl.kind][tool.decl.size]
        entries.append(
            {
                "screw": screw,
                "tool": f"hex {hex_size:g} mm",
                "status": "blocked" if hits else "clear",
                "hits": hits,
                "location": location,
            }
        )
    return entries
