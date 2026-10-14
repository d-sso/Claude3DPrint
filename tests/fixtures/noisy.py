"""Fixture: a model that prints debug output; stdout must still be pure JSON."""
from build123d import *

print("debug: loading noisy model")
PARAMETERS = {"size": 10.0}


def build(p):
    print("debug: building", p)
    with BuildPart() as part:
        Box(p["size"], p["size"], p["size"])
    return part.part
