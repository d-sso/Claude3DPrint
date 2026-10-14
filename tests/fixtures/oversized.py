"""Fixture: a 300 mm bar, longer than the 246 mm usable bed."""
from build123d import *

PARAMETERS = {"length": 300.0}


def build(p):
    with BuildPart() as part:
        Box(p["length"], 10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part
