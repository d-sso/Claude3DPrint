"""Fixture: expected() says 20 mm tall but the box is 15 mm."""
from build123d import *

PARAMETERS = {"height": 15.0}


def build(p):
    with BuildPart() as part:
        Box(40, 30, p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part


def expected(p):
    return {"main": (40, 30, 20)}
