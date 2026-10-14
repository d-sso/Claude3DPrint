"""Fixture: two parts, a base and a lid."""
from build123d import *

PARAMETERS = {"length": 40.0, "width": 30.0}


def build(p):
    with BuildPart() as base:
        Box(p["length"], p["width"], 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with BuildPart() as lid:
        with Locations((0, 0, 10)):
            Box(p["length"], p["width"], 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return {"base": base.part, "lid": lid.part}
