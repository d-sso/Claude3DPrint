"""<Project name>: assembly checks.

Derived from <design document>, confirmed with the user on <date>. Read references/assemblies.md first.
Unit frame: <where the origin is, which way is up>. Parts are modelled in print orientation, so a placement
usually rotates or flips them: place(z=..., rx=180) or flip().
"""
from print3d_assembly import (
    Assembly, Bolted, Clear, Gap, Layout, Nut, NutPocket, Part, Passage, Position, Screw, Seat, Size, Snap,
    StandIn, Step, Unit, Washer, flip, place,
)
from print3d_shared import shared  # noqa: F401  (shared values from interfaces.py: S = shared("wall", ...))

MODEL = "parts/part-name/part-name.py"  # relative to this file
SRC = "<design document> § <section>"  # every interface and target needs a source; never a value read from the model

PARAMETERS = {
    # "screw_len": 12.0,  # mm, assembly-level choice (settable with `print3d assemble --set`)
}


def build_assembly(a):
    items = [
        Part("part", MODEL, at=place(), color=(200, 200, 200)),
        # Screw("screw", "M3", "socket", a["screw_len"], origin=(0, 0, 10), direction=(0, 0, 1)),  # origin = head seat
        # Nut("nut", "M3", "hex", origin=(0, 0, 2), direction=(0, 0, 1)),  # origin = the nut's far face
        # StandIn("strip", Box(40, 10, 3), at=place(z=5)),  # needs: from build123d import Box
        # Render-only options (see references/rendering.md), on Part/StandIn/Screw/Nut/Washer:
        #   material="metal" | "translucent" | "emissive", glow=15.0 (emissive only), explode=(0, 0, 30),
        #   and Screw(..., tool=False) to skip the driver. Example: StandIn("strip", Box(40, 10, 3), material="emissive")
    ]
    interfaces = [
        # Bolted("bolt", "screw", [("part", "clearance")], nut="nut", source=SRC),
        # Snap(...), Seat(...), Passage(...), Clear(...): see references/assemblies.md
    ]
    targets = [
        # Size("height", ["part"], "z", 20.0, source=SRC),  # value None fails as "no target"
    ]
    notes = [
        # "Document conflict: <A says 3 mm, B says 4 mm>; the checks use <A> (confirmed by the user <date>)",
    ]
    # Render options: Unit("unit", ..., render=Render(backdrop="wall", facing=(0, -1, 0))) (import Render), and on a Layout
    # looks={"c1": {"strip": {"color": "#ff0000"}}} for a per-instance look.
    return Assembly([Unit("unit", items, interfaces, targets)], layouts=[], notes=notes)
