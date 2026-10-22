"""Block: 29.6 x 20 x 10 with a 12 x 4 through-slot along X, its centre slot_z (+ slot_dz) above the underside.

Frame: underside at z = 0, centred on X and Y.
"""
from build123d import *
from print3d_shared import shared

S = shared("block_x", "slot_z")

PARAMETERS = {
    "slot_dz": 0.0,  # mm, moves the slot up (a variant that blocks the strip)
}

BLOCK_Y = 20.0
BLOCK_Z = 10.0
SLOT_Y = 12.0
SLOT_Z = 4.0


def build(p):
    with BuildPart() as part:
        Box(S["block_x"], BLOCK_Y, BLOCK_Z, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, S["slot_z"] + p["slot_dz"])):
            Box(S["block_x"], SLOT_Y, SLOT_Z, mode=Mode.SUBTRACT)
    return part.part
