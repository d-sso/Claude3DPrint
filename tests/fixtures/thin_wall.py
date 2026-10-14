"""Fixture: open-top 40 x 30 x 15 box shelled to 0.5 mm walls (below min_wall 0.8, above min_feature 0.4)."""
from build123d import *

PARAMETERS = {"length": 40.0, "width": 30.0, "height": 15.0, "wall": 0.5}


def build(p):
    with BuildPart() as part:
        Box(p["length"], p["width"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
        offset(amount=-p["wall"], openings=part.faces().sort_by(Axis.Z)[-1])
    return part.part


def expected(p):
    return {"main": (p["length"], p["width"], p["height"])}
