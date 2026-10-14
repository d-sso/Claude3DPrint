"""Fixture: T shape. The 40 mm crossbar on a 10 mm stem leaves 300 mm^2 of flat underside (overhang warn)."""
from build123d import *

PARAMETERS = {}


def build(p):
    with BuildPart() as part:
        Box(10, 10, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, 10)):
            Box(40, 10, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part


def expected(p):
    return {"main": (40, 10, 15)}
