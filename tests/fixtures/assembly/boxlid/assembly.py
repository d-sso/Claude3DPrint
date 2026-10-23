"""Box and lid: two parts, two M3 bolts with nuts, two snaps and a battery stand-in.

Unit frame: the base's underside at z = 0, centred on X and Y (see the stack-up in parts/base/base.py).
The lid is modelled plate-down, so it is flipped and its plate lands on the rim: plate 20..23, skirt 16..20.
Screws go down through the lid (direction +Z points from tip to head); the seating plane is the lid top at 23.
A nut's origin is its z = 0 face in the hardware frame, the face away from the head: with direction +Z the
nut runs from 11.9 up to the pocket roof at 14.3.
"""
from build123d import Box

from print3d_assembly import (
    Assembly, Bolted, Clear, NutPocket, Nut, Part, Screw, Seat, Size, Snap, StandIn, Step, Unit, place,
)
from print3d_shared import shared

S = shared("box_x", "box_y", "wall", "bolt_dx", "groove_z")

PARAMETERS = {
    "screw_len": 12.0,  # mm, length of both M3 socket screws
}

RIM = 20.0  # base height: the lid plate sits on the rim
PLATE = 3.0  # lid plate thickness
NUT_FAR = 11.9  # the nut's far face (pocket roof 14.3 minus the 2.4 nut height)
SRC = "fixture.md § Box"


def build_assembly(a):
    dx, wall, by = S["bolt_dx"], S["wall"], S["box_y"]
    top = RIM + PLATE
    groove = RIM - S["groove_z"]
    inner_y = by / 2 - wall
    items = [
        Part("base", "parts/base/base.py", color=(200, 200, 200)),
        Part("lid", "parts/lid/lid.py", at=place(z=top, rx=180), color=(90, 140, 220)),
        StandIn("battery", Box(20, 10, 4), at=place(z=6, rz=90), color=(220, 180, 40)),  # on pads, 2 mm up
    ]
    interfaces = []
    for side, sx in (("left", -dx), ("right", dx)):
        items += [
            Screw(f"screw-{side}", "M3", "socket", a["screw_len"], origin=(sx, 0, top), direction=(0, 0, 1)),
            Nut(f"nut-{side}", "M3", "hex", origin=(sx, 0, NUT_FAR), direction=(0, 0, 1)),
        ]
        interfaces += [
            Bolted(f"bolt-{side}", f"screw-{side}", [("lid", "clearance"), ("base", "clearance")],
                   nut=f"nut-{side}", source=SRC),
            NutPocket(f"nut-{side}", f"nut-{side}", "base", source=SRC),
        ]
    for side, sy in (("front", -1), ("back", 1)):
        interfaces.append(
            Snap(f"snap-{side}", "lid", "base", point=(0, sy * inner_y, groove), normal=(1, 0, 0),
                 insert_dir=(0, 0, -1), engagement=(0.3, 0.4), clearance=(0.1, 0.2), source="fixture.md § Snap")
        )
    interfaces += [
        Seat("lid-seat", "lid", "base", point=(0, -(by / 2 - wall / 2), RIM), normal=(0, 0, 1), source=SRC),
        Clear("battery-clear", "battery", "base", min_distance=1.0, source="fixture.md § Battery"),
    ]
    targets = [
        Size("height", ["base", "lid"], "z", 23.0, source=SRC),
        # probe from +x towards -x at the lid plate's and the base wall's mid-heights: the outer faces line up
        Step("lid-flush", "lid", "base", origin_a=(40, 0, RIM + PLATE / 2), origin_b=(40, 0, RIM / 2),
             direction=(-1, 0, 0), value=0.0, source=SRC),
    ]
    return Assembly([Unit("box", items, interfaces, targets)])
