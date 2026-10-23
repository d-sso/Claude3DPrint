"""Base: an open box with two nut bosses and a snap groove for the lid.

Print notes: floor on the bed, nut pockets open to the bed (hex roof bridges 5.8 mm), no supports.

Screw/nut stack-up (assembled; Z from the base's underside, the 3 mm lid plate sits on the rim at z = 20):
  lid plate top ............... 20 + 3 = 23.0
  M3x12 tip (12 under head) ... 23 - 12 = 11.0     M3x6 tip ... 23 - 6 = 17.0
  boss top .................... 15.7  (the lid skirt ends at 16.0, so 0.3 mm of air between them)
  nut pocket roof ............. 14.3  (the screw pulls the nut up against it)
  nut (ISO 4032 M3, 2.4 high) . z = 11.9 .. 14.3
  M3x12: tip 11.0 is 0.9 mm past the nut's far face (11.9)  -> needs >= 0.5, ok
  M3x6:  tip 17.0 is 2.7 mm above the nut's top face (14.3)  -> does not reach the nut
"""
from build123d import *
from print3d_profile import nut_pocket, screw_clearance
from print3d_shared import shared

S = shared("box_x", "box_y", "wall", "bolt_dx", "groove_z")

PARAMETERS = {}

HEIGHT = 20.0
BOSS_D = 9.0
BOSS_TOP = 15.7
POCKET_TOP = 14.3
GROOVE_DEPTH = 0.6
GROOVE_H = 1.2


def build(p):
    bx, by, wall, dx = S["box_x"], S["box_y"], S["wall"], S["bolt_dx"]
    af, _ = nut_pocket("M3")
    with BuildPart() as part:
        Box(bx, by, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((0, 0, wall)):
            Box(bx - 2 * wall, by - 2 * wall, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
        for sx in (-dx, dx):
            with Locations((sx, 0, 0)):
                Cylinder(BOSS_D / 2, BOSS_TOP, align=(Align.CENTER, Align.CENTER, Align.MIN))
        for sx in (-dx, dx):
            with Locations((sx, 0, 0)):
                with BuildSketch(Plane.XY):
                    RegularPolygon(radius=af / 3 ** 0.5, side_count=6)
                extrude(amount=POCKET_TOP, mode=Mode.SUBTRACT)
                Cylinder(screw_clearance("M3") / 2, HEIGHT, align=(Align.CENTER, Align.CENTER, Align.MIN),
                         mode=Mode.SUBTRACT)
        # snap groove in the two long inner walls, centred groove_z below the rim
        zc = HEIGHT - S["groove_z"]
        for sy in (-1, 1):
            with Locations((0, sy * (by / 2 - wall + GROOVE_DEPTH / 2), zc)):
                Box(bx - 2 * wall - 2.0, GROOVE_DEPTH, GROOVE_H, mode=Mode.SUBTRACT)
    return part.part


def expected(p):
    return {"main": (S["box_x"], S["box_y"], HEIGHT)}
