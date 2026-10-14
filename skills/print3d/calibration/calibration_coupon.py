"""Calibration coupon: measures hole compensation, printed-to-printed fits and thin-wall limits for a profile.

Print notes: plate flat on the bed, peg standing on its end, no supports. PLA+ at normal settings.
Print both parts together; run `print3d calibrate` for the measuring instructions.
"""
from build123d import *
from print3d_profile import hole

RAW_HOLES = (3.0, 4.0, 5.0, 8.0)  # mm, printed at exact nominal size to measure compensation
SOCKET_CLEARANCES = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50)  # mm, diametral, on top of hole()
FIN_THICKNESSES = (0.4, 0.6, 0.8, 1.2)  # mm
PEG_D = 10.0  # mm, raw nominal shaft

PARAMETERS = {
    "plate_x": 100.0,  # mm
    "plate_y": 60.0,  # mm
    "plate_z": 5.0,  # mm
    "fin_height": 10.0,  # mm above the plate
    "socket_depth": 4.0,  # mm, blind
    "label_depth": 0.4,  # mm, engraved
    "label_size": 3.0,  # mm text height
    "chamfer": 0.5,  # mm, bottom edges (elephant's foot) and peg ends
}

HOLE_X = (-42.0, -32.0, -21.0, -7.0)  # row at y = +20
FIN_X = (8.0, 18.0, 28.0, 38.0)  # fins centred on y = +20, 10 mm long in Y
SOCKET_X = (-36.0, -20.0, -4.0, 12.0)  # two rows of four, at y = 0 and y = -17
SOCKET_ROWS_Y = (0.0, -17.0)


def _label(value):
    return f"{value:g}"


def build(p):
    top = p["plate_z"]
    labels = []  # (x, y, text)
    with BuildPart() as coupon:
        Box(p["plate_x"], p["plate_y"], top, align=(Align.CENTER, Align.CENTER, Align.MIN))
        chamfer(coupon.edges().group_by(Axis.Z)[0], length=p["chamfer"])

        for x, d in zip(HOLE_X, RAW_HOLES):
            with Locations((x, 20.0, 0.0)):
                Cylinder(radius=d / 2, height=top, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            labels.append((x, 12.0, _label(d)))

        for i, c in enumerate(SOCKET_CLEARANCES):
            x, y = SOCKET_X[i % 4], SOCKET_ROWS_Y[i // 4]
            with Locations((x, y, top - p["socket_depth"])):
                Cylinder(radius=hole(PEG_D + c) / 2, height=p["socket_depth"],
                         align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            labels.append((x, y - 8.5, _label(c)))

        for x, t in zip(FIN_X, FIN_THICKNESSES):
            with Locations((x, 20.0, top)):
                Box(t, 10.0, p["fin_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
            labels.append((x, 12.0, _label(t)))

        with BuildSketch(Plane.XY.offset(top)):
            for x, y, text in labels:
                with Locations((x, y)):
                    Text(text, font_size=p["label_size"], align=(Align.CENTER, Align.CENTER))
        extrude(amount=-p["label_depth"], mode=Mode.SUBTRACT)

    with BuildPart() as peg:
        with Locations((p["plate_x"] / 2 + 15.0, 0.0, 0.0)):
            Cylinder(radius=PEG_D / 2, height=10.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
        chamfer(peg.edges().filter_by(GeomType.CIRCLE), length=p["chamfer"])

    return {"coupon": coupon.part, "peg": peg.part}
