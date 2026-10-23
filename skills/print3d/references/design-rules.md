# Design rules: FDM, 0.4 mm nozzle, PLA+

Read this before modelling any part that mates with hardware or with another printed part. Numbers that come
from the printer profile are given as helper calls (`from print3d_profile import ...`): **use the helper, never
the number**, so recalibration updates every part. Everything else is a rule of thumb.

## 1. Walls and features
- Minimum wall: `profile_value("checks.min_wall")` (0.8 mm = 2 perimeters). Load-bearing walls: 1.2-1.6 mm.
- Nothing thinner than `profile_value("checks.min_feature")` (one nozzle width). It won't print.
- Prefer wall thicknesses that are multiples of the nozzle width (0.8, 1.2, 1.6 ...), so the slicer fills them cleanly.

## 2. Overhangs and bridges
- Faces up to `profile_value("checks.overhang_angle")` (45 degrees) from vertical print without supports.
- Turn flat undersides into 45 degree chamfers. Give horizontal holes a teardrop or diamond top.
- Bridges of 10 mm or less are reliable; up to 20 mm prints with cosmetic sag. Never put a precision feature on a long bridge.

## 3. Holes
- **Vertical holes** (axis along Z) print undersize. Use `hole(d)` for a hole that must print as diameter `d`, and
  `screw_clearance("M3")` for screw clearance holes.
- Hole compensation is measured with gauge ladders (drill-bit shanks in plain holes) at about 3-5 mm. Other sizes
  are approximate.
- **Horizontal holes** print oval and sag at the top. Use a teardrop profile, or print undersize and drill.
- For precision bores, design `hole(d - 0.1)`, then ream to `d`.

## 4. Fits between printed parts
Clearances are **diametral** (hole diameter - shaft diameter). They go on the **hole side**: never shrink the shaft.

| Fit | Helper | Use for |
|---|---|---|
| press | `fit("press")` | pins that should stay put, alignment dowels |
| snug | `fit("snug")` | lids and caps that hold by friction, removable |
| sliding | `fit("sliding")` | hinges, axles, sliding lids |
| loose | `fit("loose")` | parts that drop in, or coarse alignment |

Compensation, so nothing is compensated twice:
- `fit(kind)` is a clearance added to the shaft diameter inside `hole()`: `hole(shaft_d + fit(kind))`.
- `screw_clearance()` is **already compensated**. Don't wrap it in `hole()`.
- `insert_hole()` and `nut_pocket()` are **not** compensated and must **not** be wrapped in `hole()`. They are
  calibrated as-is (vendor CAD sizes and pocket allowances).

- **Rectangular pockets and slots:** each inner dimension = the mating outer dimension + `fit(kind)`. Fits are diametral
  (total), so that is fit/2 per side. Don't wrap straight-walled pockets in `hole()`: compensation is measured on round
  vertical holes, so treat flat-wall fits as approximate and test-fit the critical ones.
- The fit values were measured with a ~10 mm vertical round printed pin, so other sizes and orientations are approximate.
- Fits are measured **between printed parts**, so they include the printed shaft's own error. **Negative fits are
  normal** when printed shafts come out undersize: `hole(shaft_d + fit(kind))` then draws the hole smaller than the
  nominal shaft. Don't "fix" a negative fit.
- **Metal shafts** (dowel pins, drill rod, bearings, screws) don't shrink, so never use `fit()` for them. Use `hole(d)`
  for a snug or press fit and `hole(d + 0.1)` to `hole(d + 0.2)` for sliding, then test-fit.

Example: a socket for a printed D6 pin that should slide: `hole(6 + fit("sliding"))`. The fit values are measured
with the coupon sockets that already include the hole compensation, so `hole()` is the right wrapper.

## 5. Print orientation
- Put the largest flat face on the bed.
- Layers are weakest in Z: orient the part so the main load runs **along** the layers, not across them.
- Holes that must be round should be vertical.
- State the orientation in the hand-off print notes.

## 6. Hardware
- **Heat-set inserts:** use `insert_hole("M3")`. Make the hole depth the insert length + 1 mm, keep at least 1.5 mm of wall
  around the insert, and add a 0.5 mm x 45 degree lead-in chamfer.
- **Captive nuts:** `af, h = nut_pocket("M3")`. Use a hexagonal pocket from the top (`RegularPolygon(radius=af / 2, side_count=6, major_radius=False)`),
  or a slot from the side. For a pocket the nut drops into from above, add a 0.2-0.4 mm sacrificial bridge layer that you drill through.
- **Self-tapping screws** into plastic: a pilot of `hole(d_minor)` (e.g. about 2.5 mm for an M3 thread-forming screw), and at least 2.5 x d of engagement.

Snippets checked against build123d 0.13. With `major_radius=False` the polygon `radius` is the inradius (half the
across-flats distance), so `radius=af / 2` gives exactly `af` across flats:
```python
af, h = nut_pocket("M3")
top = part.faces().sort_by(Axis.Z)[-1]
with BuildSketch(top):
    RegularPolygon(radius=af / 2, side_count=6, major_radius=False)
extrude(amount=-h, mode=Mode.SUBTRACT)           # nut pocket from the top face
with Locations(top):
    Hole(radius=screw_clearance("M3") / 2)        # screw clearance hole
with Locations(top):
    Hole(radius=insert_hole("M3") / 2, depth=5)   # heat-set insert pilot, depth measured from the top surface (insert length + 1)
```

## 7. First layer
- Add a 0.4-0.6 mm x 45 degree chamfer to bottom edges. It cancels elephant's foot, so mating faces stay true:
  `chamfer(part.edges().group_by(Axis.Z)[0], length=0.5)`.
- No fillets on bottom edges, because a fillet is a growing overhang. Use a chamfer instead.

## 8. Text and small detail
- Embossed text: at least 0.6 mm high and at least 5 mm tall. Engraved text: at least 0.4 mm deep.
- Put text on top faces (best) or vertical faces. Text on the underside smears.

## 9. Snap-fits and flexures
- Keep strain low: a cantilever at least 5x as long as it is thick, with a fillet at the root.
- The beam's **length** must lie in the XY plane (print the cantilever flat), so the bending stress runs along the layers.
  Never make a cantilever that stands vertically: it delaminates.
- Expect PLA+ snaps to fatigue. Prefer screws for parts that are opened often.

## 10. Fastener engagement
The assembly checks (`print3d assemble`, `references/assemblies.md`) use these rules. Ask the user before relaxing one.
- **Nyloc nut:** the screw tip must protrude at least 1 pitch past the nut's far face (the face away from the head), so the nylon ring is engaged. M3 pitch is 0.5 mm.
- **Hex nut:** the tip must be at least flush with the far face (protrusion >= 0). Aim for 1 pitch.
- **Thread or heat-set insert part:** the screw must engage at least 2 x pitch of the part's hole: the hole must start at least 2 x pitch before the screw tip.
- **Thread part:** only the pilot engages, i.e. the depths where the hole is narrower than the screw's major diameter (M3: under 3.0 mm). A counterbore or clearance-size hole doesn't count.
- The nut must sit on the shank, between the head's seating plane and the tip.
- A bridge layer left over a nut pocket or hole blocks the screw: drill it through in the model.

## 11. Snap engagement
- Snap engagement is how far the bump reaches past the catch edge: the bump height minus the per-side running clearance.
- A typical PLA+ skirt snap engages 0.3 to 0.8 mm. Less than 0.3 mm holds poorly; more than 0.8 mm risks cracking the skirt or making the lid hard to remove.
- Declare the range in `Snap(engagement=(0.3, 0.8), ...)` with its source, and **test-fit** the first print: engagement depends on the printer's calibrated fits (`print3d calibrate`).
