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
- `print3d check <model.py> [--set key=value ...] [--check key=value ...] [--strict]`: builds and checks without writing any files.
- `print3d calibrate [--profile NAME]`: builds the calibration coupon for the active printer profile and prints measuring instructions.
- `print3d profile show|set KEY=VALUE ...|reset [KEY ...] [--profile NAME]`: shows the active profile, or records/removes calibrated values. Never hand-edit `config.toml`.
- `--profile NAME` on `run`/`check`/`calibrate`/`profile` uses another printer profile for that command. A new material or nozzle needs its own profile file and its own calibration.

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
    - `sealed`: the mesh is open or inside out. Close the shape or remove zero-thickness faces. Wall, thin and overhang are skipped until this passes.
    - `size`: the build doesn't match `expected(p)`. Fix the geometry, or fix `expected()` if the intent changed.
    - `bed_fit`: too big for the bed. Scale down, split into parts, or reorient.
    - `wall` (below the profile's `min_wall`): thicken the wall. `thin` (below `min_feature`): thicken or remove the feature, because it will not print.
  - `warn` → overhangs past the profile's `overhang_angle`. Chamfer the underside, reorient the part, or accept supports, and say which in the print notes.
  - For how to fix each kind of fault, see `references/design-rules.md` (walls, overhangs and bridges, orientation).
  - The limits come from the active printer profile (`checks.thresholds` and `checks.sources` show the values and where they came from). Never lower a threshold to make a check pass:
    - Printer-wide calibrated values go in the profile with `print3d profile set checks.min_wall=...`, and only when the user has measured them.
    - For a deliberate thin feature in one model (a flex hinge, a breakaway tab), add `CHECKS = {"min_wall": 0.6}` to that model with a comment giving the reason.
  - Thickness is measured with rays from the surface, so it can read high at sharp inside corners. If the parameters say a wall is thin, trust the parameters.
- <!-- #4: render and inspect the preview here -->

Repeat until the numbers match. Don't hand over a part whose dimensions you haven't checked.

## 4. build123d API
Use `references/build123d-cheatsheet.md` for the API and the common pitfalls. Don't rely on remembered build123d
syntax: the API changes between versions, and the cheat-sheet is tested against the installed version.

## 5. Hand-off
First run `print3d check <model.py> --strict`; it must exit with code 0. Then tell the user:
- `checks.status`, and how each overhang warning was handled
- the output paths (STL per part and the combined 3MF)
- the final dimensions (from `bbox_mm`) and the key parameters
- how to change it, with examples: `print3d run <model.py> --set length=80 --set wall=2.4`
- your print notes: orientation and whether supports are needed
- the active printer profile (`profile.name` in the result) and any tolerance worth test-fitting first
- if `print3d profile show` reports no "override" sources, suggest `print3d calibrate` before relying on tight fits

## 6. Calibration
When the user reports coupon measurements, follow the `instructions` returned by `print3d calibrate` to convert them.
Each fit key is set to the label value of the chosen socket, and `holes.compensation` is an absolute value, not an adjustment.
Calibration is two-pass: record `holes.compensation` first and, if it moved by 0.05 or more, rerun `print3d calibrate`
and reprint before recording the fits (check `profile.used["holes.compensation"]` matches).
If a helper is a `PARAMETERS` default and you override that parameter with `--set`, `profile.used` still lists the helper's value.
Confirm the values with the user, then record them with `print3d profile set key=value ...` and show the result.
Overrides are stored as quoted dotted keys under `[profile_overrides.<name>]`; `profile set` refuses sub-table forms.
