"""Fixture: a plain 40 x 30 x 15 box sitting on the bed (volume 18000 mm^3)."""
from build123d import *

PARAMETERS = {
    "length": 40.0,  # mm
    "width": 30.0,  # mm
    "height": 15.0,  # mm
    "copies": 1,  # unused; exercises int parameters
    "solid": True,  # unused; exercises bool parameters
    "label": "box",  # unused; exercises str parameters
}


def build(p):
    with BuildPart() as part:
        Box(p["length"], p["width"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part


def expected(p):
    return {"main": (p["length"], p["width"], p["height"])}
