---
name: print3d
description: Design 3D-printable parts as parametric build123d models and export STL/3MF. Use for 3D printing, STL or 3MF files, brackets, enclosures, holders, adapters, replacement parts, and requests like "model a…" or "print a…".
---

# print3d: parametric parts for 3D printing

You design parts as **parametric build123d models** (Python) and build them with the `print3d` runner. The runner
exports printable files and reports results as JSON. Never hand-write STL, and never guess geometry you can measure.

## Running print3d
- Bash (Git Bash): `~/.claude/skills/print3d/scripts/print3d <command> ...`
- PowerShell: `& "$HOME\.claude\skills\print3d\scripts\print3d.cmd" <command> ...`
- If the output says `print3d is not installed`, tell the user to run `skills/print3d/scripts/setup.ps1` from the repo.

Commands:
- `print3d new <part-name>`: creates `<parts_dir>/<part-name>/<part-name>.py` from the template. Names are kebab-case.
- `print3d run <model.py> [--set key=value ...] [--formats stl,3mf,step] [--out DIR]`: builds the model and exports to `<model dir>/out/`.
- `run` also checks printability. Add `--check key=value` to override a threshold, `--strict` to exit with code 3 on a failed check, or `--no-check` to skip the checks.
- `run` also renders a preview sheet per part to `out/<model>.preview.png` (`out/<model>-<part>.preview.png` for multi-part designs). `--no-preview` skips it.
- `print3d check <model.py> [--set key=value ...] [--check key=value ...] [--strict]`: builds and checks without writing any files.
- `print3d calibrate [--set gauges=...] [--profile NAME]`: builds the calibration coupon for the active printer profile and prints measuring instructions. `--set gauges=3.175,3.969,5.159` sets the measured gauge (drill-bit shank) diameters.
- `print3d profile show|set KEY=VALUE ...|reset [KEY ...] [--profile NAME]`: shows the active profile, or records/removes calibrated values. Never hand-edit `config.toml`.
- `print3d assemble <assembly.py> [--set key=value ...] [--level unit|layout|all] [--unit NAME] [--layout NAME] [--strict] [--out DIR] [--no-sheets] [--profile NAME]`: checks how parts fit together (section 7).
- `--profile NAME` on `run`/`check`/`calibrate`/`assemble`/`profile` uses another printer profile for that command. A new material or nozzle needs its own profile file and its own calibration.

## 1. Gather requirements
Before modelling, make sure you know the following. Ask **one question at a time**, and only for what isn't already clear:
- what the part is for and where it goes
- key dimensions, and the parts it must fit or mate with (measure them: get real numbers in mm)
- fits: what must slide, press-fit, or take screws (which size?)
- load and print orientation: what face sits on the bed, and which direction must be strong
- how many parts, and how they're assembled

State your assumptions back to the user in a short list before building.
If the part mates with hardware or another part, read references/design-rules.md before modelling.

## 2. Set up the model
1. Run `print3d new <part-name>` and edit the file it creates.
2. Put **every design decision in `PARAMETERS`**, in mm, with a comment. Nothing is hard-coded in `build()`. Derive any values that aren't free choices from parameters inside `build()`.
3. Take every printer-dependent number from the profile: `from print3d_profile import fit, hole, screw_clearance, insert_hole, nut_pocket, profile_value`. Never hard-code a clearance, hole compensation, insert hole or nut pocket. Helpers may be used as PARAMETERS defaults, so they stay `--set`-able.
4. Fill in the docstring: the part's purpose and print notes (orientation, supports).
5. `build(p)` returns `part.part`, or `{"name": part.part, ...}` for multi-part designs.
6. Define `expected(p)`: the outer size of each part (mm), computed from parameters. The size check compares the build against it.

## 3. Build loop
Model in **phases**, and run after each one, so any error points to the last change:
1. base shape → 2. features (holes, pockets, bosses) → 3. finishing (fillets, chamfers)

After each run, read the JSON:
- `ok: false` → look at `error.stage` and `error.line`, fix that line, and re-run.
  `params` errors list the valid parameters, and `export` errors usually mean the file is open in a slicer.
- `ok: true` → **check the numbers against your intent**:
  - `bbox_mm.size`: do all three dimensions match what the part should measure?
  - `bbox_mm.min[2]` should be `0`, meaning the part sits on the bed.
  - `volume_mm3`: is it plausible? A hollow part should be well below its bounding-box volume, and holes should reduce volume by about πr²h each.
  - `warnings` must be empty. If not, the geometry is invalid; simplify the last change.
- **Printability checks** (`checks` in the JSON) run on every `run`. Read `checks.status`:
  - `fail` → fix the geometry and re-run. Each failing check has a `message`, and its `regions[].center` is a point on the part (mm) that locates the feature:
    - `sealed`: the mesh is open, inside out, or non-manifold (two bodies touching only along an edge). Close the shape or remove zero-thickness faces; separate touching bodies or fuse them with real overlap. Wall, thin and overhang are skipped until this passes.
    - `size`: the build doesn't match `expected(p)`. Fix the geometry, or fix `expected()` if the intent changed.
    - `bed_fit`: too big for the bed. Scale down, split into parts, or reorient.
    - `wall` (below the profile's `min_wall`): thicken the wall. `thin` (below `min_feature`): thicken or remove the feature, because it will not print.
  - `warn` → overhangs past the profile's `overhang_angle`. Chamfer the underside, reorient the part, or accept supports, and say which in the print notes.
  - For how to fix each kind of fault, see `references/design-rules.md` (walls, overhangs and bridges, orientation).
  - The limits come from the active printer profile (`checks.thresholds` and `checks.sources` show the values and where they came from). Never lower a threshold to make a check pass:
    - Printer-wide calibrated values go in the profile with `print3d profile set checks.min_wall=...`, and only when the user has measured them.
    - For a deliberate thin feature in one model (a flex hinge, a breakaway tab), add `CHECKS = {"min_wall": 0.6}` to that model with a comment giving the reason.
  - Thickness is measured with rays from the surface, so it can read high at sharp inside corners and on curved thin shells (a thin dome, tube or bowl wall). If the parameters say a wall is thin, trust the parameters.
- **Visual review**: every `run` renders a preview sheet per part (`parts[].files.preview`, a PNG). Open it with the Read tool and check it, describing what the sheet shows, not what you expect:
  1. Shape: in iso, front, right and top, every required feature is there, on the correct face and side. Count holes, bosses, slots and ribs against the parameters.
  2. Bed: the bottom sits flat on the bed in front and right, and the orientation matches the print notes.
  3. Hollows: in each section the orange cut face shows plausible walls with material on the intended side, holes go through or stop as intended, and there are no unexpected voids or solid cavities.
  4. Proportions: use the scale bar to compare features with their parameter values.
  5. Markers: each numbered marker (a failing or warning check region, listed in the info panel) sits on the feature its check describes.
  6. Coverage: the sections are centre cuts. If a key hole, snap or boss isn't crossed by any cut, say so and judge it from the views.
  - Reading the sheet: front looks along +Y, right along −X, top down −Z; iso is from the front-right-top corner. The sections cut through the bbox centre (y, x, z) and remove the half nearest the viewer. All ortho views and sections share one scale. A hollow marker is hidden behind the part.
  - A mismatch between the parameters and the picture is a finding even when every check passes: fix it and re-run. A preview is current only if its path is in the latest result (old PNGs can stay in `out/`). If `warnings` says the preview failed, you have no visual check.
  - The sheet doesn't show fit between parts or features thinner than about a pixel; for those the `wall` and `thin` checks are the authority.

Repeat until the numbers and the sheet match. Don't hand over a part whose dimensions you haven't checked.

## 4. build123d API
Use `references/build123d-cheatsheet.md` for the API and the common pitfalls. Don't rely on remembered build123d
syntax: the API changes between versions, and the cheat-sheet is tested against the installed version.

## 5. Hand-off
First run `print3d check <model.py> --strict`; it must exit with code 0. Then tell the user:
- `checks.status`, and how each overhang warning was handled
- the output paths (STL per part, the combined 3MF and the preview sheets)
- the visual review: one line per checklist item that needed a judgement (e.g. "Section z = 12.5: wall 2.4 mm as intended"), or that no visual check was done because no preview is in the latest result
- the final dimensions (from `bbox_mm`) and the key parameters
- how to change it, with examples: `print3d run <model.py> --set length=80 --set wall=2.4`
- your print notes: orientation and whether supports are needed
- the active printer profile (`profile.name` in the result) and any tolerance worth test-fitting first
- if `print3d profile show` reports no "override" sources, suggest `print3d calibrate` before relying on tight fits

## 6. Calibration
When the user reports coupon measurements, follow the `instructions` returned by `print3d calibrate` to convert them.
Calibration takes one print. Gauges are drill-bit shanks measured with calipers: `print3d calibrate --set gauges=3.175,3.969,5.159`.
`holes.compensation` comes from the gauge ladders and is an absolute value, not an adjustment.
Each fit is the chosen socket's label corrected for the new compensation, `fit = c + used - new`, where `used` is
`profile.used["holes.compensation"]` of the build that was printed (its `out/calibration_coupon.result.json`; read it
before re-running `print3d calibrate`, which overwrites it). Fits may be negative (printed shafts print undersize).
If a helper is a `PARAMETERS` default and you override that parameter with `--set`, `profile.used` still lists the helper's value.
Confirm the values with the user, then record them with `print3d profile set key=value ...` and show the result.
Overrides are stored as quoted dotted keys under `[profile_overrides.<name>]`; `profile set` refuses sub-table forms.

## 7. Assemblies
Use this for projects whose parts mate with each other or with hardware. Read `references/assemblies.md` first; it has the declaration API, the workflow and how to read results.
1. Derive the checks from the design document: units, layouts, interfaces, hardware and stand-ins, targets, each with its source section.
2. **Ask** for any value that matters but is missing or unclear (a bought component's real size). Never guess it.
3. Present the list, with any document conflicts, and **confirm** it with the user before writing `assembly.py`.
4. Never take an expected value from the model being checked. A failing target is a finding: raise it, never adjust the target to match the model.
5. Record confirmed document conflicts in `Assembly(notes=[...])` so they appear in `warnings`.
6. Run `print3d assemble`, and review every evidence sheet before hand-off, describing what it shows.
7. A project may mark its root with `assembly.py` before it declares units: `Assembly(units=[])` passes with a warning. `--unit`/`--layout` runs keep the other groups' sheets. `run`/`check` warn when a shared name does not come from `shared()`.
