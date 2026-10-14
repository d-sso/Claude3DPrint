"""Fixture: expected() raises on line 14."""
from build123d import *

PARAMETERS = {"length": 40.0}


def build(p):
    with BuildPart() as part:
        Box(p["length"], 30, 15, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part


def expected(p):
    return {"main": (p["lenght"], 30, 15)}
