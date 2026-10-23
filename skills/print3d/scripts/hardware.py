"""Catalogue of simplified fastener solids (screws, nuts, washers) for assembly checks.

Local frame, shared by every part: +Z is the axis pointing from the screw tip towards the head.
- screw: origin at the head's seating plane; head above z = 0 (countersunk: cone below z = 0, top face at z = 0);
  shank is a plain cylinder at the nominal major diameter (no threads) down to z = -length.
- nut / washer: from z = 0 up along +Z.
"""
import math
from dataclasses import dataclass

from build123d import Plane, Solid, Vector, Wire, Face

import print3d_profile

SCREW_KINDS = ("socket", "countersunk", "button")
NUT_KINDS = ("hex", "nyloc")

PITCH = {"M2": 0.4, "M2.5": 0.45, "M3": 0.5, "M4": 0.7, "M5": 0.8}  # ISO 261 coarse pitch

# Head diameter dk and height k, in mm.
HEADS = {
    # ISO 4762 socket head cap screw
    "socket": {
        "M2": {"dk": 3.8, "k": 2.0},
        "M2.5": {"dk": 4.5, "k": 2.5},
        "M3": {"dk": 5.5, "k": 3.0},
        "M4": {"dk": 7.0, "k": 4.0},
        "M5": {"dk": 8.5, "k": 5.0},
    },
    # DIN 7991 countersunk (flat) head socket screw, 90 degree head; the standard starts at M3
    # ISO 10642 heads are larger (dk up to 6.72 for M3 against 6.0 here), so a countersink should leave room
    "countersunk": {
        "M3": {"dk": 6.0, "k": 1.7},
        "M4": {"dk": 8.0, "k": 2.3},
        "M5": {"dk": 10.0, "k": 2.8},
    },
    # ISO 7380-1 button head socket screw; the standard starts at M3
    "button": {
        "M3": {"dk": 5.7, "k": 1.65},
        "M4": {"dk": 7.6, "k": 2.2},
        "M5": {"dk": 9.5, "k": 2.75},
    },
}

# DIN 985 prevailing-torque (nyloc) hex nut, total height h
NYLOC_H = {"M2": 2.8, "M2.5": 3.4, "M3": 4.0, "M4": 5.0, "M5": 5.0}

# ISO 7089 plain washer, normal series: inner d1, outer d2, thickness h
WASHERS = {
    "M2": {"d1": 2.2, "d2": 5.0, "h": 0.3},
    "M2.5": {"d1": 2.7, "d2": 6.0, "h": 0.5},
    "M3": {"d1": 3.2, "d2": 7.0, "h": 0.5},
    "M4": {"d1": 4.3, "d2": 9.0, "h": 0.8},
    "M5": {"d1": 5.3, "d2": 10.0, "h": 1.0},
}
# Hex nuts: ISO 4032 across-flats / height live in print3d_profile (ISO4032_AF, ISO4032_M).


@dataclass
class Fastener:
    size: str
    kind: str
    length: float
    shape: Solid
    pitch: float
    head_top: float  # z of the head's top face (0.0 for countersunk)
    tip: float  # z of the shank tip (-length)


def _diameter(size):
    return float(size[1:])


def _choose(what, valid, value):
    if value not in valid:
        raise ValueError(f"unknown {what} {value!r}; valid: {', '.join(valid)}")


def _cyl(radius, height, z0=0.0):
    return Solid.make_cylinder(radius, height, Plane.XY.offset(z0))


def screw(size, kind, length):
    """A screw stand-in (see the module docstring for the local frame). For countersunk, `length` includes the head."""
    _choose("screw kind", SCREW_KINDS, kind)
    _choose(f"{kind} screw size", list(HEADS[kind]), size)
    if length <= 0:
        raise ValueError(f"screw length must be positive, got {length}")
    d = _diameter(size)
    head = HEADS[kind][size]
    if kind == "countersunk":
        depth = (head["dk"] - d) / 2  # 90 degree cone from head diameter down to the shank
        cone = Solid.make_cone(d / 2, head["dk"] / 2, depth, Plane.XY.offset(-depth))
        return _cyl(d / 2, length, -length).fuse(cone).clean()
    return _cyl(d / 2, length, -length).fuse(_cyl(head["dk"] / 2, head["k"])).clean()


def nut(size, kind="hex"):
    """A hex nut stand-in with a bore at the nominal diameter, from z = 0 up."""
    _choose("nut kind", NUT_KINDS, kind)
    _choose("nut size", [s for s in print3d_profile.ISO4032_AF if s in PITCH], size)
    h = print3d_profile.ISO4032_M[size] if kind == "hex" else NYLOC_H[size]
    r = print3d_profile.ISO4032_AF[size] / math.sqrt(3)  # corner radius
    pts = [Vector(r * math.cos(math.radians(60 * i)), r * math.sin(math.radians(60 * i)), 0) for i in range(6)]
    body = Solid.extrude(Face(Wire.make_polygon(pts, close=True)), Vector(0, 0, h))
    return body.cut(_cyl(_diameter(size) / 2, h)).clean()


def washer(size):
    """An ISO 7089 washer stand-in, from z = 0 up."""
    _choose("washer size", list(WASHERS), size)
    w = WASHERS[size]
    return _cyl(w["d2"] / 2, w["h"]).cut(_cyl(w["d1"] / 2, w["h"])).clean()


def fastener(size, kind, length):
    """A screw wrapped in a Fastener record."""
    shape = screw(size, kind, length)
    head_top = 0.0 if kind == "countersunk" else HEADS[kind][size]["k"]
    return Fastener(size, kind, length, shape, PITCH[size], head_top, -float(length))
