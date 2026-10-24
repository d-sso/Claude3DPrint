# Rendering: what it looks like, and can it be put together

`print3d render` turns a model or an assembly into a Blender scene (`scene.blend`, which the user orbits) and preset
PNG stills (which you read). It never changes `run`, `check` or `assemble` results. Needs Blender 4.2+; nothing else does.
Never write `bpy` code for a project: every Blender step lives in the skill's scripts.

## When to render
- After `print3d run` (a part) or `print3d assemble` (an assembly) passes. Render the passing design, not a guess.
- For look and feel (colours, materials, a glowing part behind a diffuser) and for assemblability (how the parts
  stack, whether a screwdriver reaches each screw). Rendering is not a check: it adds no pass/fail to `assemble`.
- Not for dimensions or fits: those are `run` / `assemble` findings.

## Running
```
print3d render <assembly.py | model.py> [--set k=v ...] [--level unit|layout|all] [--unit NAME] [--layout NAME]
    [--draft | --final] [--view V ...] [--lighting L ...] [--state S ...] [--resolution WxH] [--out DIR]
    [--profile NAME] [--open]
```
- `--set`, `--profile`, `--unit`, `--layout`, `--level` mean what they mean in `assemble` (`--level` and `--unit` /
  `--layout` only apply to an assembly.py). A file named `assembly.py` is an assembly; any other `.py` is a model.
- One group per rendered thing: a model is one group, an assembly gives one per unit and one per layout.
- Output, under `<model dir>/out` (or `--out`): `render/<group folder>/scene.blend` and
  `<state>-<view>-<lighting>.png`, plus `render.result.json`. Group folders: `unit-<name>`, `layout-<name>`, and the
  file name without `.py` for a model (odd characters become `_`).
- Each run first deletes the old PNGs and `scene.blend` of the groups it selects. The `_staging` folder inside a
  group is removed after a success and kept after a failure.
- Default stills per group (9): assembled `iso` x `studio`, `hard`, `dim`; assembled `front`, `right`, `top` x
  `studio`; exploded `iso`, `front` x `studio`; `tools` view x `studio`.
- `--view` (`iso`, `front`, `right`, `top`, `tools`), `--lighting` (`studio`, `hard`, `dim`) and `--state`
  (`assembled`, `exploded`, `tools`) can be repeated. Giving any of them renders the product of what you gave, with
  `iso`, `studio` and `assembled` for the axes you left out. Stills that don't apply are dropped silently: no
  exploded state when nothing moves, no `tools` when there is no screw tool.
- `--draft` (default): EEVEE, 1280x960, 32 samples. `--final`: Cycles (GPU if available, else CPU), 1920x1440, 256
  samples. `--resolution WxH` overrides the size.
- Time, rough guides on an RTX 3070 Ti for a small two-part box, not promises: the 9 draft stills took about 10 s end to
  end, the final ones about 5 minutes. Bigger scenes take longer (the limit is 3600 s per group). Draft first.
- `--open` opens the first `.blend` in the Blender window when done.
- Blender is found from the config key `blender`, then `PRINT3D_BLENDER`, then
  `C:\Program Files\Blender Foundation\Blender *\blender.exe` (newest first). If none is 4.2+, the command fails
  with exit code 1 and an install hint. Exit codes: 0 ok, 1 error, 2 usage.
- Read `out/render.result.json`: `ok`, `error` (`stage`: `usage`, `blender` or `load`, or a model stage), `quality`
  (`name`, `engine`, `device`, `resolution`, `samples`), `blender` (`path`, `version`), `timings_s`, `warnings`, and
  `groups[]`: `kind`, `name`, `folder`, `blend`, `images[]` (`state`, `view`, `lighting`, `path`), `tool_access[]`, `warnings`.

## Declaring looks
All fields are optional and render-only. Parts and stand-ins default to `matte` (plastic), hardware to `metal`.
```python
material="matte" | "metal" | "translucent" | "emissive"   # Part, StandIn, Screw, Nut, Washer
color="#rrggbb" | (r, g, b)                                # as before
glow=15.0                                                  # emissive only: emission strength, default 10
```
- `translucent` lets light through, tinted by `color`. `emissive` lights itself in `color`; `glow` on any other material
  is a declaration error.
- `Layout(looks={instance: {item: {"color": .., "material": .., "glow": ..}}})` changes the look of one item of
  one instance. It is render-only: no geometry change, no variant, not part of `exact`. Instance and item names are
  validated like `overrides`; any item type works, hardware included. `assemble` only validates it.
  A look entry that gives only `glow` changes the brightness of an item that is already emissive. If the merged look
  isn't emissive, `assemble` still passes, but `render` fails with a load error naming the layout, instance and item.
  A look with only `color` keeps the item's material and glow.
- `Layout.looks` reaches unit instances. A layout's own items take the look they were declared with.
- A model.py declares one look for all its parts with a module-level dict (keys: `color`, `material`, `glow`,
  `backdrop`, `facing`): `RENDER = {"color": "#d9a066", "material": "matte"}`. Without it, parts get the palette
  colours in order, matte, on a floor. A bad key or value is a `load` error.
- Backdrop: `Unit(..., render=Render(backdrop="wall", facing=(0, -1, 0)))` (also on `Layout`; `RENDER` has the same two
  keys for a model). The default is `"floor"`. `facing` is the side of the object that faces away from the wall and is
  required for `"wall"`; it also becomes the group's up axis for the exploded view and the camera views.
  Each state gets its own backdrop, at the lowest point of what that state shows (along up for a floor, along
  `facing` for a wall). So a screw pulled down in the exploded view, or a driver pointing down, stays above the floor.

A glowing strip behind a diffuser, with some instances recoloured:
```python
from build123d import Box
from print3d_assembly import Assembly, Layout, Part, Render, StandIn, Unit, place

wall = Render(backdrop="wall", facing=(0, -1, 0))
panel = Unit("panel", [
    Part("frame", "parts/frame/frame.py", color="#2b2b2b"),
    StandIn("strip", Box(60, 8, 2), at=place(z=4), material="emissive", color="#ffffff", glow=15),  # needs a real size
    Part("diffuser", "parts/diffuser/diffuser.py", at=place(z=8), material="translucent", color="#f2f2f2"),
], render=wall)
group = Layout("group", [(f"p{k}", "panel", place(x=k * 70)) for k in range(6)],
               looks={f"p{k}": {"strip": {"color": "#ff3030"}} for k in range(3)},   # instances p0..p2 glow red
               render=wall)
```
Judge the glow in the `dim` lighting, with `--final` (see "Reading the images").

## Exploded view
Each item moves by an offset (mm, in the unit frame; layout frame for layout items). The exploded state exists only
when some offset is non-zero.
- `explode=(dx, dy, dz)` on `Part`, `StandIn`, `Screw`, `Nut`, `Washer` sets the offset; it wins over the rule below.
  `repeat` turns it with the copy's rotation, like `direction`.
- Default rule. Up is +Z (the `facing` direction for a wall); `step` = half the group's size along up, at least 10 mm.
  - Parts and stand-ins are ranked by the bottom of their bounding box along up. Items resting at the same height
    (bottoms within 0.01 mm) move together: they share one rank, and the distinct heights get ranks 0, 1, 2, ...
    Rank `r` moves `r * step` along up. So things resting inside a container rank above it and lift out of it.
  - Hardware moves like the nearest part (origin to bounding box), plus its own pull along its axis: a screw out
    along its direction by its length + 10 mm, a washer 5 mm, a nut 10 mm the opposite way.
- A layout keeps every unit instance whole: instances do not move. Only layout-level items (strip, cleat, hardware)
  explode, among themselves by the same rule. No layout items means no exploded state.
- A model.py has no exploded state: it has no offsets.
- Override only when the default stacks badly (a part that should slide sideways, not lift).

## Tool access
Every `Screw` gets a straight driver on its axis, out of the head along `direction`. `Screw(..., tool=False)` opts
out (a screw driven before another part goes on). Nuts and washers get none.
- Driver: hex tip 10 mm, round shaft (hex size + 1.5 mm across, at least 4 mm) to 75 mm above the head top, then a handle
  25 mm across and 90 mm long. Hex size by head: socket M2 1.5, M2.5 2, M3 2.5, M4 3, M5 4; button and countersunk
  M3 2, M4 2.5, M5 3 (mm across flats).
- `blocked`: the driver overlaps any placed item other than its own screw and a washer on its axis. `hits` lists the
  items and `location` is where the first overlap is (mm). `clear`: it reaches the head. The status never changes an
  `assemble` result; a blocked driver adds a warning and is drawn red, a clear one dark grey.
- A unit group checks its own screws. A layout group checks only layout-level screws, against everything placed
  including the instances. Unit screws are not re-checked in each instance: read them from the unit group.
- Only a straight driver is modelled. A screw that an L-key could reach from the side may show as blocked.

## Reading the images
Read every PNG and describe what it shows. File names are `<state>-<view>-<lighting>.png`.
- `assembled-iso-studio`: overall form, colours and materials as intended, parts that look wrong or missing.
- `assembled-iso-hard`: one strong light with crisp shadows: edges, steps, gaps, proud or recessed features.
- `assembled-iso-dim`: low light so glow and translucency read: does the lit part show through the diffuser, is the
  light even, does anything leak. A draft only approximates light through translucent parts: say so, and judge diffusion
  from a `--final` render of this still.
- `assembled-front-studio`, `-right-`, `-top-`: straight-on views for alignment, spacing and flushness.
- `exploded-iso-studio`, `exploded-front-studio`: how the parts stack, order and direction, overlapping or crowded layers.
- `tools-tools-studio`: each screw's driver. Say which are red (blocked), what they hit and where. In this still tall
  handles can look darker: the lights are sized from the assembled object, not the drivers.

## Opening the scene
Give the user the `groups[].blend` path from `render.result.json` (one `.blend` per group), or run with `--open`. In Blender:
- Scenes (top right) are the lightings: `studio`, `hard`, `dim`.
- View layers are the states: `assembled`, `exploded`, `tools`. Switch layer to switch state.
- Cameras are `cam-<view>`: `cam-iso`, `cam-front`, `cam-right`, `cam-top`, `cam-tools`. Pick one as the scene camera or
  orbit freely.
- Collections: `assembled`, `exploded` (only when something moves), `tools`, `backdrop-assembled`,
  `backdrop-exploded`, `backdrop-tools` (each view layer shows only its own state's backdrop), `cameras`, and
  `lights-<lighting>`. The scene is in metres.

## Agent workflow
1. After `run` / `assemble` passes, run `print3d render <file>` (draft).
2. Read every PNG and describe: the overall look, colours and materials, how the exploded view stacks, and every
   blocked tool with what it hits. Note that draft `dim` is approximate.
3. A blocked tool is a finding to raise with the user. Never move a screw, drop `tool=False` in, or change a part quietly
   to make it go away.
4. Give the user the `.blend` path(s) and how to open them, and offer a `--final` render of the views they care about
   (for example `--final --view iso --lighting dim`).

## Limitations
- Only a straight driver is modelled (no L-key from the side, no angled access).
- Draft light through translucent parts is approximate; `--final` judges diffusion. Neither is photometry: no filament
  model, no layer lines.
- The default explode rule is a heuristic. A model.py, and a layout without layout-level items, has no exploded state.
- Lights are sized from the assembled object, so tool stills can be unevenly lit.
- Layouts render every instance's full geometry (meshes are shared per unit), so big layouts are slow in `--final`.
- No animation or assembly sequence.
