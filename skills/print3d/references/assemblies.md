# Assemblies: checking that parts fit together

`print3d assemble` checks parts as an assembled whole: holes line up, screws reach their nuts, snaps engage, nothing
overlaps unintentionally, and the result matches the design document. It checks **nominal** geometry only (no
tolerance stack-up) and exports no print files; `print3d run` still builds, checks and exports each part.

Use it for projects whose parts mate with each other or with hardware. The skill has no project-specific checks:
you work out the checks from the project's design document, confirm them with the user, then declare them.

## Workflow
1. **Derive the checks.** Read the design document(s). List, each with its source section:
   units (and their variants), layouts, interfaces by type, hardware and stand-ins, targets.
   Include checks that span units wherever the design runs something across them (a strip through slots in
   neighbouring units, a bolt between tiles).
2. **List conflicts** between documents, or between a document and the models, and say for each whether it affects a check.
3. **Ask** for any value that matters but is missing or unclear (a bought component's real size, a bolt position). Never guess it.
4. **Present the list and get the user's confirmation before writing `assembly.py`.** The confirmed list is the spec.
   Propose the `exact` subset of every layout (see Layouts) in the same list.
5. Create or update `interfaces.py` and move the parts onto it. Run `print3d run` on each part: geometry, checks and `bbox_mm` must be unchanged.
6. Write `assembly.py` (start from `templates/assembly_template.py`). Record each confirmed conflict in `Assembly(notes=[...])`; the notes appear in the result's `warnings`.
7. Run `print3d assemble <assembly.py>`. Read **every** evidence sheet with the Read tool and describe what it shows.
8. Fix or raise each failure (see Disagreements). Hand off: status per level, each failure and how it was resolved, the targets table, the warnings, the sheet paths.

### Disagreements
- A check that fails because the document and the model disagree is a finding. Ask the user which is right, then change the document or the model. **Never change a target to match the model.**
- Every interface and target has a `source`: a design-document section (`"design.html § Joining"`), a `design-rules.md` rule, or `"confirmed by user 2026-10-21"`. Never a value read from the model being checked.
- A target with `value=None` fails as "no target", so a missing number shows up.
- Document conflicts go in `Assembly(notes=...)`; never settle one quietly.

## Project layout
```
project/
  assembly.py      declaration (print3d_assembly)
  interfaces.py    INTERFACES: shared values
  parts/<part>/<part>.py
```
The project root is the nearest folder above a model that contains `assembly.py`. `print3d run <part>.py` finds `interfaces.py` there.

## Shared values
- A value that more than one part depends on is defined **once** in `interfaces.py`: `INTERFACES = {"wall": 2.0,  # mm, box wall}`.
- A part takes it with `shared(...)` as its `PARAMETERS` defaults, **under the same name**:
  `from print3d_shared import shared`, then `PARAMETERS = {**shared("wall", "box_x"), "bump_h": 0.5}`. It can still be `--set` for that part alone.
- Derived values (`inner = box_x - 2 * wall`) are computed in `build()` (or a function), never stored, so they follow an override.
- Moving existing parts onto `interfaces.py` may rename or remove part parameters, as long as the part's geometry, checks and `bbox_mm` stay the same. Update docstrings and `--set` examples.
- `print3d assemble --set name=value` accepts only `INTERFACES` names and the assembly's own `PARAMETERS`; the value changes in every part using it. Anything else is a params error.

## The declaration
`assembly.py` defines `PARAMETERS` (optional) and `build_assembly(a)`, which returns `Assembly(units, layouts=[], notes=[])`.
Every field is validated when the object is made, so a typo fails at its line. Positions are (x, y, z) mm in the unit frame (layout frame for layout items).
Working examples: `tests/fixtures/assembly/boxlid/` (unit) and `row/` (layout) in the repo; `templates/assembly_template.py` here.

```python
from build123d import Box
from print3d_assembly import (Assembly, Bolted, Clear, Gap, Layout, Nut, NutPocket, Part, Passage, Position,
                              Screw, Seat, Size, Snap, StandIn, Step, Unit, Washer, flip, linear, place, polar, repeat)

items = [
    Part("base", "parts/base/base.py", color=(200, 200, 200)),
    Part("lid", "parts/lid/lid.py", at=place(z=23, rx=180), params={"holes": True}),   # modelled plate-down
    Screw("screw", "M3", "socket", 12.0, origin=(14, 0, 23), direction=(0, 0, 1)),     # origin = head's seating plane
    Nut("nut", "M3", "hex", origin=(14, 0, 11.9), direction=(0, 0, 1)),                # origin = nut's far face
    Washer("washer", "M3", origin=(14, 0, 11.4), direction=(0, 0, 1)),               # origin = washer's far face
    StandIn("battery", Box(20, 10, 4), at=place(z=6, rz=90)),
]
```
- `place(x, y, z, rx=, ry=, rz=)` is a `Location` (rotate rx, ry, rz in that order, then move); `flip()` rotates 180 degrees about X. Parts are modelled in print orientation, so a placement usually flips or rotates them.
- `Part(name, model, at, params, color)`: `model` is relative to `assembly.py`; `params` override the part's PARAMETERS (variants); `"_part": "<name>"` picks one shape of a multi-part model. One model may be placed more than once.
- Hardware is placed by its axis: `direction` is the screw's +Z, from tip towards head. `origin` is the head's seating plane for a `Screw`, and the face away from the head for a `Nut` or `Washer` (it runs from `origin` along `+direction`). Kinds: screw `socket`/`countersunk`/`button`; nut `hex`/`nyloc`. Sizes: M2, M2.5, M3, M4, M5 for socket screws, nuts and washers; countersunk and button heads exist for M3, M4, M5 only. Threads aren't modelled (a screw is a cylinder at its nominal diameter).
- `Nut(..., angle=30)` turns the hex about its own axis (degrees, right-handed about `direction`) so it lines up with its pocket. At `angle=0` one corner points along the nut frame's local +X: +X for `direction` +Z, -X for -Z, +Z for +X and +Y, -Z for -X and -Y (build123d's `Plane(origin, z_dir=direction)`). `repeat` turns the angle with each copy.
- A `StandIn` is a straight build123d shape made in `assembly.py`. Ask the user for its real size.
- `color` is `(r, g, b)` or `"#rrggbb"`; hardware defaults to grey.

### Interfaces
Every interface takes `source=` (keyword) and an optional `limit=` (mm), but only `Bolted` (axis offset, nut axis offset), `NutPocket` (axis offset) and `Seat` (gap) use it, with default 0.1. `Snap`, `Passage` and `Clear` accept it and ignore it.
```python
Bolted("bolt", "screw", [("lid", "clearance"), ("base", "thread")], source=SRC)  # roles: clearance, thread, insert
Bolted("bolt", "screw", [("lid", "clearance"), ("base", "clearance")], nut="nut", protrusion=1.0, source=SRC)
Bolted("bolt", "screw", [("lid", "clearance")], washer="washer", source=SRC)  # nut_washer= for one under the nut
NutPocket("nut-pocket", "nut", "base", source=SRC)
Snap("snap", "lid", "base", point=(0, -11, 18), normal=(1, 0, 0), insert_dir=(0, 0, -1),
     engagement=(0.3, 0.8), clearance=(0.1, 0.2), travel=10.0, source=SRC)
Seat("seat", "lid", "base", point=(0, -14, 20), normal=(0, 0, 1), source=SRC)
Passage("strip", "strip", ["c1.block", "c2.block"], min_clearance=0.4, direction=(1, 0, 0), source=SRC)  # layout-level: dotted names resolve only in a Layout
Clear("battery-clear", "battery", "base", min_distance=1.0, source=SRC)
```
- **Bolted**: for each listed part, finds the hole on the screw's axis and checks its offset (<= limit) and its diameter by role (`clearance`: >= `screw_clearance(size)` - 0.1; `insert`: `insert_hole(size)` +/- 0.1; `thread`: only the pilot counts, see below), that the head seats (no gap, no overlap), that the shank overlaps nothing, and that the screw is long enough (`design-rules.md` "Fastener engagement"). The nut, if given, must sit on the shank. Whether the nut fits its pocket is a separate `NutPocket` check. `protrusion=` overrides the rule's minimum.
  - `thread` role: only depths where the hole is narrower than the screw's major diameter (the pilot, e.g. 2.5 for M3) count towards engagement. A thread part with no such depth within the screw's length (a clearance-size hole, or a counterbore the tip ends in) fails as `not found: no pilot hole smaller than M3 in <part> ...`.
  - Nut depths: where a declared nut sits on the axis (within the axis limit), the depths it spans aren't measured as any part's hole (the pocket would skew the offset); `excluded` lists them as "not measured: the nut sits here". What is left must still be a hole for min(1.0 mm, the material left near the axis) along it, so a nut trap in a thin part (3 mm part, 2.4 mm pocket) is measured on its last 0.6 mm. The nut's depths are measured if they are all the material near the axis.
  - `washer=`: the head seats on that `Washer` (head to washer top <= 0.1), and the washer seats on the part below it (<= 0.1, measured from its bottom face). `nut_washer=` (needs `nut=`): the washer touches the part above it (<= 0.1). Each washer must be on the screw axis (<= limit) and overlap no listed part, and the shank isn't checked against it. An undeclared washer under a head reads as a head gap. A countersunk head with `washer=` fails.
- **NutPocket**: the nut sits in the pocket without overlap, can't turn (pocket across-flats below the nut's across-corners), and is on its screw's axis.
- **Snap**: measured in the section through `point` with `normal`; `insert_dir` is the direction the bump part moves to snap in and lies in the section plane. `engagement` and `clearance` are `(min, max)` in mm or `None`. Engagement = how far the bump reaches past the catch edge = bump height minus the per-side running clearance. A typical PLA+ skirt is 0.3 to 0.8 mm (`design-rules.md` "Snap engagement"). `fit="name"` reports a profile fit alongside the clearance.
- **Seat**: faces of `a` and `b` near the plane (`point`, `normal`) touch. The gap is measured along `normal` through `point`.
- **Passage**: the stand-in is taken as **straight**. Its run line (bounding-box centre, along `direction` or the longest bounding-box axis) must pass through an opening of **each** part in `through`, with at least `min_clearance` and no overlap. One opening per part is enough (a cable through one wall of a box, a strip ending inside a tray), see "Passage" below.
- **Clear**: `a` and `b` keep at least `min_distance` apart. Any overlap fails, whatever `min_distance` is: `min_distance=0` means "touching is fine, overlapping isn't".

### Targets
- `Size(name, items, axis, value, source=, tol=0.1)`: bounding size of the items along "x", "y" or "z".
- `Gap(name, a, b, direction_or_None, value, source=)`: distance between two items (None: smallest distance).
- `Step(name, a, b, origin_a, origin_b, direction, value, source=)`: probe from each origin along `direction`; 0 means flush. `a` and `b` may be the same item (two faces of one part).
- `Position(name, item, origin, direction, value, source=)`: where a probe first hits the item.
- A bound instead of a value: `value=None` with `min=` and/or `max=` (mm), e.g.
  `Gap("battery-gap", "battery", "base", None, None, min=2.5, source=SRC)`. It passes iff min <= measured <= max
  (no tolerance; an open end is unbounded) and `expected` is `{"min": .., "max": ..}`. Give either `value` (with `tol`) or bounds, never both.
- No value and no bounds fails as "no target".

### Patterns
Repeated hardware and joints are declared once and copied with `repeat(template, locations, name="{name}-{i}")`:
```python
from print3d_assembly import linear, polar, repeat

ring = polar(6, center=(0, 0, 0))          # 6 rotations about +Z: start_deg=0, step_deg=360/6, axis=(0, 0, 1)
items += repeat(Screw("screw", "M3", "socket", 12.0, origin=(30, 0, 23), direction=(0, 0, 1)), ring)  # screw-0..5
interfaces += repeat(Bolted("bolt-{i}", "screw-{i}", [("tray", "clearance"), ("frame", "thread")], source=SRC), ring)
targets += repeat(Gap("edge-{i}", "c{i}.tile", "frame", None, None, min=2.5, source=SRC), linear(4, (0, 0, 0)))
```
- `polar(n, start_deg=0, step_deg=360/n, axis=(0, 0, 1), center=(0, 0, 0))`, `linear(n, (dx, dy, dz), start=(0, 0, 0))` and
  `grid(nx, ny, dx, dy, start=(0, 0, 0))` return lists of Locations; any list of Locations (e.g. `place(...)`s) works.
- Each copy is the template moved by one Location: points (`origin`, `point`, `origin_a`/`origin_b`) move,
  directions and normals (`direction`, `normal`, `insert_dir`) only turn, a `Part`/`StandIn` `at` becomes `loc * at`,
  and a `Size` axis turns with the rotation (it must stay X, Y or Z).
- Names: `name` may use `{name}` (the template's name) and `{i}` (0-based index); a template name containing `{i}` is used as is.
  A reference to another item (`screw`, `nut`, `through`, `a`, `b`, `item`, `bump`, `catch`, `items`, `part`) containing `{i}`
  gets the same index; one without it is shared by every copy (`"tray"` above). Any other `{...}` is an error.
- Copies are validated like hand-written declarations; the result is a plain list, so use it in any `Unit` or `Layout` list.

### Layouts
```python
cell = Unit("cell", [Part("block", "parts/block/block.py")])
row = Layout("row", [(f"c{k + 1}", "cell", place(x=k * 20)) for k in range(4)],
             items=[StandIn("strip", Box(80, 10, 3), at=place(x=30, z=5))],
             interfaces=[Passage("strip", "strip", [f"c{k + 1}.block" for k in range(4)], 0.4, source=SRC)],
             targets=[Gap("seam-12", "c1.block", "c2.block", None, 0.4, source=SRC)],
             exact=("c1", "c2"), overrides={"c2": {"block": {"variant": True}}})
Assembly([cell], [row], notes=[...])
```
- Inside a layout, `"<instance>.<item>"` names an item of a unit instance; a bare name is a layout item. Item names contain no `.`.
- **`exact`** lists the instances checked with full solids. It **must include every unit variant** (every instance with `overrides`) and **every distinct neighbour pair**. Propose it to the user with the checks.
- Variants get their own unit interfaces checked. Layout interfaces are `skipped` (listed with a reason, not failed) only when they name no layout item and touch an instance outside `exact`. An interface that names a layout item (e.g. a strip `StandIn`) is always checked against all the parts it names. Layout targets run on every instance.
- A layout `Passage` is the way to check a strip running through slots of neighbouring units.

## Running
`print3d assemble <assembly.py> [--set k=v ...] [--level unit|layout|all] [--unit NAME] [--layout NAME] [--strict] [--out DIR] [--no-sheets] [--profile NAME]`. `--profile` picks the printer profile whose `screw_clearance` and `insert_hole` values the checks use.
Exit codes: 0 ok (checks may still fail), 1 error, 2 usage, 3 failing checks under `--strict`.

## Reading the result
`out/assembly.result.json`: `ok`, `error`, `status` ("pass"/"fail"), `summary` (interface pass/fail/skipped counts, overlaps, targets), `warnings` (declared notes, sheet problems), `units[]` and `layouts[]`.
Each group lists `interfaces[]` (name, type, `status`, `measured`, `expected`, `source`, `location`, `message`, `evidence` paths), `overlaps[]` (volume and location; any overlap fails), `targets[]`, and for layouts `skipped[]`.
- A `message` starting `not found:` means the feature the check measures (a hole on the axis, a snap bump) was not found. A check never passes on nothing: look at the sheet and the declared point or axis.
- `no target:` means the value is missing.

### Hole finding (Bolted)
- Holes: closed holes, straight closed slots, and notches whose material covers at least 200 degrees.
- Not holes: open channels, fork prongs, gaps between bodies, islands inside a window.
- Material crossing the screw's axis within its length blocks it, even a sacrificial bridge layer: drill it through in the model.
- Only depths within the screw's length count; depths beside unrelated walls are excluded.
- Known safe false fails ("not a hole"): curved or arc slots, slots with a nick in the edge, a slot with an unrelated pin inside.

### Passage
- Each body (solid) of the part is looked at on its own: separate bodies never make one opening together, even where they touch. A part built as a compound of unfused pieces can therefore fail; fuse it.
- A body's material within reach of the run line splits into depth ranges: its pieces there, merged where their depths overlap or touch. A range is an opening the stand-in passes when:
  - it lies within the stand-in's length, or the body runs on past a stand-in end but the stand-in doesn't end inside an opening there;
  - the run line crosses no material of the part in it;
  - its **shadow** along the line (the body's sections in the range and within the stand-in's length, 0.25 mm apart, put together) surrounds the line by the hole rules above, and the widest circle in it contains the line.
- The shadow is what finds a slot in a wall at an angle to the line: no single cross-section closes round the line there.
- Material beyond the stand-in's ends doesn't count, so a box's far wall on the line is fine.
- A stand-in ends inside an opening when the section just inside its end (0.2 mm in, or half way into the body when it enters less) surrounds the line (a rod stopping in a hole, however shallow), or when an opening's shadow runs across its end (a strip stopping inside a slanted slot). That fails.
- `measured`: `opening` (the depth range used, from the stand-in's centre), `aperture` (the widest circle's diameter in the shadow), `clearance`, `overlap_mm3`.
- U-clips and troughs (about 290 degrees of cover) count as openings.
- Known safe false fail: a stand-in that stops within its reach (half its cross-section diagonal + `min_clearance` + 1 mm) past a slanted opening.

## Evidence sheets
PNGs in `out/assembly/<unit or layout>/<check>.png`, plus one overview per unit and layout (iso, front, right, top, colour legend). Each item has its own colour; hardware is grey. Read each and say what it shows:
- **red** = a measured overlap (a fault);
- **teal hatch** = intended engagement (a screw in its thread or insert part);
- **magenta cross** = the check's location.

The sheet is drawn from the geometry that was measured. If it doesn't show the feature the message describes, the check isn't measuring what you think.

`out/assembly/` is managed through `out/assembly/.print3d-sheets.json`: each run deletes only the sheets that file lists, then rewrites it. Without the manifest nothing is deleted and `warnings` says sheets there may be stale. Old PNGs can remain, so use only the paths in the latest result.
