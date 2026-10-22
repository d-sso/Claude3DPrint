"""Lid: a 3 mm plate with a skirt that snaps into the base's groove.

Print notes: modelled plate-down (print orientation), skirt up; the bumps are 45-degree ridges, no supports.
"""
from build123d import *
from print3d_profile import screw_clearance
from print3d_shared import shared

S = shared("box_x", "box_y", "wall", "bolt_dx", "groove_z")

PLATE = 3.0
SKIRT_H = 4.0
SKIRT_T = 1.2
SKIRT_GAP = 0.15  # mm per side between the skirt and the walls

PARAMETERS = {
    "hole_dx": 0.0,  # mm, shifts both screw holes along x
    "holes": True,  # False removes the screw holes
    "bump_h": 0.5,  # mm, snap bump height
}


def build(p):
    bx, by, wall, dx = S["box_x"], S["box_y"], S["wall"], S["bolt_dx"]
    out_x, out_y = bx - 2 * wall - 2 * SKIRT_GAP, by - 2 * wall - 2 * SKIRT_GAP
    with BuildPart() as part:
        Box(bx, by, PLATE, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, PLATE)):
            Box(out_x, out_y, SKIRT_H, align=(Align.CENTER, Align.CENTER, Align.MIN))
            Box(out_x - 2 * SKIRT_T, out_y - 2 * SKIRT_T, SKIRT_H, align=(Align.CENTER, Align.CENTER, Align.MIN),
                mode=Mode.SUBTRACT)
        # the bump lands in the groove: groove_z below the plate's underside, i.e. above PLATE in this orientation
        zc = PLATE + S["groove_z"]
        h = p["bump_h"]
        if h > 0:
            for sy in (-1, 1):
                y0 = sy * out_y / 2
                with BuildSketch(Plane.YZ.offset(-10)):
                    # profile in (y, z): a 45-degree ridge rooted slightly inside the skirt wall
                    Polygon((y0 - sy * 0.3, zc - h - 0.3), (y0, zc - h), (y0 + sy * h, zc), (y0, zc + h),
                            (y0 - sy * 0.3, zc + h + 0.3))
                extrude(amount=20)
        if p["holes"]:
            for sx in (-dx, dx):
                with Locations((sx + p["hole_dx"], 0, 0)):
                    Cylinder(screw_clearance("M3") / 2, PLATE, align=(Align.CENTER, Align.CENTER, Align.MIN),
                             mode=Mode.SUBTRACT)
    return part.part


def expected(p):
    return {"main": (S["box_x"], S["box_y"], PLATE + SKIRT_H)}
