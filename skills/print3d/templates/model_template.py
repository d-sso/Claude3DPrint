"""<Part name>: <one-line purpose>.

Print notes: <orientation on the bed, supports needed, material>.
"""
from build123d import *
from print3d_profile import fit, hole, screw_clearance, insert_hole, nut_pocket, profile_value  # printer-calibrated tolerances; never hard-code clearances

# Every design decision is a parameter (mm). Override with: print3d run <this file> --set length=60
PARAMETERS = {
    "length": 40.0,  # mm, outer size along X
    "width": 30.0,  # mm, outer size along Y
    "height": 15.0,  # mm, outer size along Z
}


def build(p):
    with BuildPart() as part:
        # Phase 1 - base shape (sits on the bed: Z from 0 up)
        Box(p["length"], p["width"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))

        # Phase 2 - features (holes, pockets, bosses)

        # Phase 3 - finishing (fillets, chamfers)

    return part.part  # or {"lid": lid.part, "base": base.part} for multi-part designs


def expected(p):
    # Outer size per part (mm), from the parameters. print3d checks the built part against it.
    return {"main": (p["length"], p["width"], p["height"])}


# Lower a check threshold only for a deliberate feature, and say why, e.g.:
# CHECKS = {"min_wall": 0.6}  # living hinge is meant to flex
