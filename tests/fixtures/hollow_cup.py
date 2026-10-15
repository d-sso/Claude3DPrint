"""Fixture: open-top 40 x 30 x 20 cup with 2 mm walls and a 3 mm floor."""
from build123d import *

PARAMETERS = {"length": 40.0, "width": 30.0, "height": 20.0, "wall": 2.0, "floor": 3.0}


def build(p):
    with BuildPart() as part:
        Box(p["length"], p["width"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, p["floor"])):
            Box(p["length"] - 2 * p["wall"], p["width"] - 2 * p["wall"], p["height"],
                align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    return part.part


def expected(p):
    return {"main": (p["length"], p["width"], p["height"])}
