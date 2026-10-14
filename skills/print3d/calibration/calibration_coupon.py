"""Calibration coupon: measures hole compensation (gauge ladders), printed-to-printed fits and thin-wall limits.

Print notes: plate flat on the bed, peg standing on its end, no supports. PLA+ at normal settings.
Print both parts together; run `print3d calibrate` for the measuring instructions.
"""
import math

from build123d import *
from print3d_profile import hole

GAUGE_OFFSETS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)  # mm, plain holes at gauge + offset, not compensated
GAUGE_LIMITS = (2.0, 8.0)  # mm, allowed gauge diameters
MAX_GAUGES = 4
SOCKET_CLEARANCES = (-0.4, -0.3, -0.2, -0.15, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.3, 0.5)  # mm, diametral, on top of hole()
FIN_THICKNESSES = (0.4, 0.6, 0.8, 1.2)  # mm
PEG_D = 10.0  # mm, raw nominal shaft

PARAMETERS = {
    "gauges": "3,4,5",  # mm, measured shank diameters of the gauges (drill bits), comma-separated, 1-4 values
    "plate_x": 160.0,  # mm
    "plate_y": 70.0,  # mm
    "plate_z": 5.0,  # mm
    "fin_height": 10.0,  # mm above the plate
    "socket_depth": 4.0,  # mm, blind
    "label_depth": 0.4,  # mm, engraved
    "label_size": 3.0,  # mm text height
    "chamfer": 0.5,  # mm, bottom edges (elephant's foot), ladder hole exits and peg ends
    "notch": 8.0,  # mm, orientation chamfer on the far-left (-X, +Y) vertical edge
}

SOCKET_X = (-68.0, -52.0, -36.0, -20.0)  # 3 rows of 4, row 1 nearest the far (+Y) edge
SOCKET_ROWS_Y = (20.0, 3.0, -14.0)
LADDER_X = tuple(8.0 + 10.0 * k for k in range(len(GAUGE_OFFSETS)))  # k = 0 on the left
LADDER_ROWS_Y = (28.0, 17.0, 6.0, -5.0)  # one slot per gauge, filled from the far edge
LADDER_LABEL_X = -4.0
FIN_X = (20.0, 32.0, 44.0, 56.0)
FIN_Y = -22.0  # fins are 10 mm long in Y, centred here
FIN_LABEL_Y = -13.0


def parse_gauges(text):
    """'3.175, 3.969' -> [3.175, 3.969]. Empty items are ignored. Raises ValueError naming the problem."""
    items = [s.strip() for s in str(text).split(",")]
    items = [s for s in items if s]
    if not items or len(items) > MAX_GAUGES:
        raise ValueError(f"gauges must list 1 to {MAX_GAUGES} diameters in mm, e.g. '3,4,5'; got {text!r}")
    lo, hi = GAUGE_LIMITS
    gauges = []
    for item in items:
        try:
            g = float(item)
        except ValueError:
            raise ValueError(f"gauges: {item!r} is not a number (in {text!r})") from None
        if not math.isfinite(g) or not lo <= g <= hi:
            raise ValueError(f"gauges: {item} mm is outside {lo:g}-{hi:g} mm (in {text!r})")
        gauges.append(g)
    return gauges


def _label(value):
    return f"{value:g}"


def build(p):
    gauges = parse_gauges(p["gauges"])
    top = p["plate_z"]
    c = p["chamfer"]
    hx, hy = p["plate_x"] / 2, p["plate_y"] / 2
    labels = []  # (x, y, text)
    with BuildPart() as coupon:
        Box(p["plate_x"], p["plate_y"], top, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with BuildSketch():
            Polygon((-hx, hy), (-hx + p["notch"], hy), (-hx, hy - p["notch"]), align=None)
        extrude(amount=top, mode=Mode.SUBTRACT)
        chamfer(coupon.edges().group_by(Axis.Z)[0], length=c)

        for y, g in zip(LADDER_ROWS_Y, gauges):
            for x, off in zip(LADDER_X, GAUGE_OFFSETS):
                r = (g + off) / 2
                with Locations((x, y, 0.0)):
                    Cylinder(radius=r, height=top, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
                    Cone(bottom_radius=r + c, top_radius=r, height=c,
                         align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            labels.append((LADDER_LABEL_X, y, _label(g)))

        for i, clearance in enumerate(SOCKET_CLEARANCES):
            x, y = SOCKET_X[i % 4], SOCKET_ROWS_Y[i // 4]
            with Locations((x, y, top - p["socket_depth"])):
                Cylinder(radius=hole(PEG_D + clearance) / 2, height=p["socket_depth"],
                         align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            labels.append((x, y - 8.5, _label(clearance)))

        for x, t in zip(FIN_X, FIN_THICKNESSES):
            with Locations((x, FIN_Y, top)):
                Box(t, 10.0, p["fin_height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
            labels.append((x, FIN_LABEL_Y, _label(t)))

        with BuildSketch(Plane.XY.offset(top)):
            for x, y, text in labels:
                with Locations((x, y)):
                    Text(text, font_size=p["label_size"], align=(Align.CENTER, Align.CENTER))
        extrude(amount=-p["label_depth"], mode=Mode.SUBTRACT)

    with BuildPart() as peg:
        with Locations((hx + 15.0, 0.0, 0.0)):
            Cylinder(radius=PEG_D / 2, height=10.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
        chamfer(peg.edges().filter_by(GeomType.CIRCLE), length=c)

    return {"coupon": coupon.part, "peg": peg.part}
