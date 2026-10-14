"""Fixture: 20 x 20 x 5 block with a 0.3 mm fin standing on it (below min_feature 0.4)."""
from build123d import *

PARAMETERS = {"fin": 0.3}


def build(p):
    with BuildPart() as part:
        Box(20, 20, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, 5)):
            Box(p["fin"], 20, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part
