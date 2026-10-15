"""Fixture: a typical part, 50 x 50 x 30 mm L-bracket with filleted edges, four screw holes and a rib."""
from build123d import *

PARAMETERS = {"size": 50.0, "height": 30.0, "thickness": 4.0, "hole": 3.4, "fillet": 1.5}


def build(p):
    s, h, t = p["size"], p["height"], p["thickness"]
    with BuildPart() as part:
        Box(s, s, t, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, s / 2 - t / 2, 0)):
            Box(s, t, h, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, t)):
            Box(t, s - t, h - t, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        fillet(part.edges().filter_by(Axis.Z), radius=p["fillet"])
        with Locations((-s / 4, -s / 4, t), (s / 4, -s / 4, t)):
            Hole(p["hole"] / 2)
        with Locations(Location((-s / 4, s / 2, h * 0.65), (90, 0, 0)), Location((s / 4, s / 2, h * 0.65), (90, 0, 0))):
            Hole(p["hole"] / 2)
    return part.part


def expected(p):
    return {"main": (p["size"], p["size"], p["height"])}
