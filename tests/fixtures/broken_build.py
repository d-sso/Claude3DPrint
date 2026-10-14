"""Fixture: build() fails inside build123d on line 10 (fillet radius too large)."""
from build123d import *

PARAMETERS = {"size": 10.0, "radius": 20.0}


def build(p):
    with BuildPart() as part:
        Box(p["size"], p["size"], p["size"])
        fillet(part.edges(), radius=p["radius"])
    return part.part
