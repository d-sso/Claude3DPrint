"""Fixture: a plate with an M3 clearance hole and a pin sized with a sliding fit."""
from build123d import *
from print3d_profile import fit, screw_clearance

PARAMETERS = {
    "clearance": fit("sliding"),  # recorded at load time
}


def build(p):
    with BuildPart() as part:
        Box(30, 20, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, 5)):
            Cylinder(radius=screw_clearance("M3") / 2, height=5, align=(Align.CENTER, Align.CENTER, Align.MAX),
                     mode=Mode.SUBTRACT)
    return part.part
