# build123d 0.13 cheat-sheet (builder mode)

Every block below is executed by `tests/test_cheatsheet.py`. If a snippet here disagrees with your memory of
build123d, **trust this file**. Units are mm. Z is up, and the bed is the plane Z = 0.

## Base shapes: sit the part on the bed
`Box`, `Cylinder` and `Sphere` are **centred on the origin** by default. Use `align` so the part sits on Z = 0.
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    Cylinder(radius=5, height=20, align=(Align.CENTER, Align.CENTER, Align.MIN))
bb = part.part.bounding_box()
assert abs(bb.min.Z) < 1e-6 and abs(bb.max.Z - 20) < 1e-6
```

## Selecting faces and edges
- `sort_by(Axis.Z)[-1]` is the top face; `[0]` is the bottom.
- `filter_by(Axis.Z)` gives edges parallel to Z.
- `group_by(Axis.Z)[0]` is everything at the lowest Z.
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    top = part.faces().sort_by(Axis.Z)[-1]
    vertical_edges = part.edges().filter_by(Axis.Z)
    bottom_edges = part.edges().group_by(Axis.Z)[0]
assert len(vertical_edges) == 4 and len(bottom_edges) == 4
assert abs(top.center().Z - 10) < 1e-6
```

## Fillets and chamfers
Apply these **last** (phase 3). A radius bigger than the adjacent faces allow raises an error: reduce it.
A 0.4–0.6 mm chamfer on the bottom edges stops "elephant's foot" from flaring the first layer.
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    fillet(part.edges().filter_by(Axis.Z), radius=3)
    chamfer(part.edges().group_by(Axis.Z)[0], length=0.5)
assert part.part.is_valid
```

## Holes drilled from a face
`Locations(face)` puts the workplane on the face, with its normal pointing out. `Hole` drills straight through by
default. Nested `Locations`/`GridLocations` offsets are measured on that face.
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations(part.faces().sort_by(Axis.Z)[-1]):
        with GridLocations(20, 0, 2, 1):  # x_spacing, y_spacing, x_count, y_count
            Hole(radius=1.7)  # M3 clearance (3.4 mm)
expected = 40 * 30 * 10 - 2 * 3.14159265 * 1.7**2 * 10
assert abs(part.part.volume - expected) < 1
```

## Countersunk and counterbored holes
For ISO 10642 (M3 countersunk), pass `counter_sink_angle=90`; the default is 82° (imperial).
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations(part.faces().sort_by(Axis.Z)[-1]):
        with Locations((-10, 0)):
            CounterSinkHole(radius=1.7, counter_sink_radius=3.25, counter_sink_angle=90)
        with Locations((10, 0)):
            CounterBoreHole(radius=1.7, counter_bore_radius=3.0, counter_bore_depth=3.0)
pi, r, R = 3.14159265, 1.7, 3.25
sink = pi * r**2 * 10 + pi / 3 * (R - r) * (R**2 + R * r + r**2) - pi * r**2 * (R - r)  # shaft + cone
bore = pi * r**2 * 10 + pi * (3.0**2 - r**2) * 3.0  # shaft + wider counterbore
assert abs(part.part.volume - (40 * 30 * 10 - sink - bore)) < 1
```

## Sketch, then extrude
Build 2D shapes in `BuildSketch`. A shape with `mode=Mode.SUBTRACT` cuts the shapes before it.
```python
from build123d import *

with BuildPart() as part:
    with BuildSketch() as profile:
        Rectangle(40, 20)
        Circle(5, mode=Mode.SUBTRACT)
    extrude(amount=5)
expected = (40 * 20 - 3.14159265 * 25) * 5
assert abs(part.part.volume - expected) < 1
```

## Pocket into a face
Sketch on the face, then extrude with a **negative** amount (into the part) and `mode=Mode.SUBTRACT`.
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with BuildSketch(part.faces().sort_by(Axis.Z)[-1]):
        Rectangle(30, 20)
    extrude(amount=-4, mode=Mode.SUBTRACT)
assert abs(part.part.volume - (40 * 30 * 10 - 30 * 20 * 4)) < 1
```

## Hollow box (shell) with an open top
`offset` with a negative amount hollows the part inwards. The faces listed in `openings` are removed.
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    offset(amount=-2, openings=part.faces().sort_by(Axis.Z)[-1])
inner = 36 * 26 * 18
assert abs(part.part.volume - (40 * 30 * 20 - inner)) < 1
```

## Revolve a profile
Sketch on `Plane.XZ`, where local x is X and local y is Z, and revolve about `Axis.Z`.
```python
from build123d import *

with BuildPart() as part:
    with BuildSketch(Plane.XZ):
        with Locations((10, 5)):
            Rectangle(4, 10)  # ring: radius 8..12, height 0..10
    revolve(axis=Axis.Z)
expected = 3.14159265 * (12**2 - 8**2) * 10
assert abs(part.part.volume - expected) < 1
```

## Subtracting primitives and placing things
Any primitive can take `mode=Mode.SUBTRACT`. Use `Locations((x, y, z))` to place it.
```python
from build123d import *

with BuildPart() as part:
    Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    with Locations((0, 0, 10)):
        Cylinder(radius=8, height=4, align=(Align.CENTER, Align.CENTER, Align.MAX), mode=Mode.SUBTRACT)
assert abs(part.part.volume - (40 * 30 * 10 - 3.14159265 * 64 * 4)) < 1
```

## Multi-part models
Build each part in its own `BuildPart` and return a dict. The keys become file names.
```python
from build123d import *

with BuildPart() as base:
    Box(40, 30, 5, align=(Align.CENTER, Align.CENTER, Align.MIN))
with BuildPart() as lid:
    Box(40, 30, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
parts = {"base": base.part, "lid": lid.part}
assert all(p.volume > 0 for p in parts.values())
```

## Common pitfalls
- **`return part` instead of `return part.part`.** `part` is the builder, while `part.part` is the shape.
- **The part floats or sinks.** Default alignment is centred, so use `align=(..., ..., Align.MIN)` on the base shape.
- **A fillet fails.** The radius is too large for the edge, or a later boolean has split the edge. Fillet last, with a smaller radius.
- **The wrong face is selected.** Selections are evaluated **at that moment**; after booleans, select again (`sort_by` and then index).
- **A cut does nothing.** You forgot `mode=Mode.SUBTRACT`, or a pocket extrude used a positive `amount` (pointing away from the part).
