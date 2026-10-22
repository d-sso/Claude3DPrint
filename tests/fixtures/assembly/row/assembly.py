"""A row of four blocks with a strip running through all their slots.

Unit `cell` = one block (frame: its underside at z = 0, centred). Layout `row`: c1..c4 at x = k * pitch, so the
seams between neighbours are pitch - block_x = 0.4. The strip (10 x 3, 4 x pitch long) is a layout stand-in
through all four slots; c1 and c2 are checked exactly.
"""
from build123d import Box

from print3d_assembly import Assembly, Gap, Layout, Part, Passage, Size, StandIn, Unit, place
from print3d_shared import shared

S = shared("pitch", "block_x", "slot_z")
SRC = "fixture.md § Row"


def build_assembly(a):
    pitch = S["pitch"]
    cell = Unit("cell", [Part("block", "parts/block/block.py", color=(200, 200, 200))])
    instances = [(f"c{k + 1}", "cell", place(x=k * pitch)) for k in range(4)]
    strip = StandIn("strip", Box(4 * pitch, 10, 3), at=place(x=1.5 * pitch, z=S["slot_z"]), color=(220, 180, 40))
    blocks = [f"c{k + 1}.block" for k in range(4)]
    row = Layout(
        "row",
        instances,
        items=[strip],
        interfaces=[Passage("strip", "strip", blocks, min_clearance=0.4, direction=(1, 0, 0), source=SRC)],
        targets=[
            Gap("seam-12", "c1.block", "c2.block", None, pitch - S["block_x"], source=SRC),
            Size("row-length", blocks, "x", 3 * pitch + S["block_x"], source=SRC),
        ],
        exact=("c1", "c2"),
    )
    return Assembly([cell], [row])
