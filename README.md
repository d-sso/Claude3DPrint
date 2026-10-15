# Claude3DPrint: the `print3d` skill

A [Claude Code](https://claude.com/claude-code) skill for designing **3D-printable parts** as parametric
[build123d](https://github.com/gumyr/build123d) models. Claude writes a small Python model, builds it with the
`print3d` runner, checks the reported dimensions, and hands you STL and 3MF files ready for your slicer.

## Requirements
- Windows 10/11 with PowerShell 5.1 or later
- Python 3.11, 3.12 or 3.13 ([python.org](https://www.python.org/downloads/))
- Claude Code

## Install
```powershell
git clone https://github.com/d-sso/Claude3DPrint.git
cd Claude3DPrint
powershell -ExecutionPolicy Bypass -File skills\print3d\scripts\setup.ps1
```
`setup.ps1` is safe to re-run. It:
1. finds Python 3.11–3.13;
2. creates a virtual environment at `%LOCALAPPDATA%\print3d\venv` and installs the pinned `requirements.txt`
   (about 750 MB, mostly the OpenCascade kernel);
3. asks for your **parts library** folder (default `~/3DParts`) and saves it in `%LOCALAPPDATA%\print3d\config.toml`;
4. links `~/.claude/skills/print3d` to `skills/print3d` in this repo (a directory junction; no admin needed);
5. builds a throwaway test part to check everything works.

Parameters: `-PartsDir <folder>` skips the prompt; `-SkipSelfTest` skips step 5.

Re-run `setup.ps1` after pulling a change to `skills/print3d/requirements.txt`.

## Use
In Claude Code, describe the part: *"model a wall bracket for a 25 mm pipe, two M4 screws"*.
Claude asks what it needs to know, creates `<parts library>/<part-name>/<part-name>.py`, builds it, and reports
the output files.

You can also run the tool yourself:
```bash
~/.claude/skills/print3d/scripts/print3d new cable-clip
~/.claude/skills/print3d/scripts/print3d run ~/3DParts/cable-clip/cable-clip.py --set length=60 --formats stl,3mf,step
```
Each run writes the model's files to `out/` next to the model, plus `<model>.result.json` with its dimensions,
volume, any errors, and the printability checks: sealed mesh, expected size, bed fit, wall thickness, thin
features and overhangs. Each run also renders a preview sheet per part, `out/<model>.preview.png`: iso, front, right
and top views and three section cuts, with axes, scale bars and numbered markers on failing check regions
(`--no-preview` skips it). To check without exporting (no files, no preview):
```bash
~/.claude/skills/print3d/scripts/print3d check ~/3DParts/cable-clip/cable-clip.py --strict
```
The bed size and the wall, feature and overhang limits come from the active printer profile (`print3d profile show`).
Record calibrated values there, for example with `print3d profile set checks.min_wall=1.0`. To override the
profile, use `[checks]` in `config.toml` (for every profile; see `config.example.toml`), a model's `CHECKS` dict,
or `--check key=value` for one run. `--no-check` skips the checks.

Exit codes: `0` ok, `1` model error, `2` usage error, `3` a check failed under `--strict`.

## Printer profile and calibration
Tolerances (hole compensation, fits, insert holes, nut pockets) and printer limits come from a **printer profile**:
one printer + nozzle + material, in `skills/print3d/profiles/`. The default is `centauri-carbon-pla-plus`.
Models read it through `from print3d_profile import fit, hole, screw_clearance, insert_hole, nut_pocket, profile_value`.

Calibrate once per profile, with one print:
```bash
~/.claude/skills/print3d/scripts/print3d calibrate --set gauges=3.175,3.969,5.159   # coupon + measuring instructions
~/.claude/skills/print3d/scripts/print3d profile set holes.compensation=0.2 fits.press=-0.2 fits.snug=-0.15
~/.claude/skills/print3d/scripts/print3d profile show       # merged values and where each comes from
~/.claude/skills/print3d/scripts/print3d profile reset fits.snug
```
Hole compensation is measured with gauge ladders: plain holes you test with drill-bit shanks (measure each shank with
calipers and pass the values with `--set gauges=`; the default is 3, 4 and 5 mm). Each fit is the label of the coupon
socket that gave that fit, corrected for the new compensation. Fits can be negative, because printed shafts come out
undersize.
Calibrated values are stored in your local `config.toml` as quoted dotted keys under `[profile_overrides.<profile>]`
(for example `"fits.snug" = 0.15`), never in the repo. `print3d profile set` and `reset` refuse sub-table forms
such as `[profile_overrides.<profile>.fits]`.
Use `--profile NAME` on `run`, `calibrate` or `profile` to work with another profile for one command.
For a new material or nozzle, copy the profile file, adjust it and calibrate it on its own.

## Model files
```python
from build123d import *

PARAMETERS = {"length": 40.0, "width": 30.0, "height": 15.0}  # mm; override with --set key=value

def build(p):
    with BuildPart() as part:
        Box(p["length"], p["width"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part  # or {"lid": ..., "base": ...} for several parts

def expected(p):  # optional: outer size per part, checked after each build
    return {"main": (p["length"], p["width"], p["height"])}
```

## Uninstall
1. Remove the link with `cmd /c rmdir "$HOME\.claude\skills\print3d"`. This only removes the junction, not the repo.
   Do not use `Remove-Item -Recurse` or Explorer's "delete folder contents" on the link: that can delete the repo's files.
2. Delete `%LOCALAPPDATA%\print3d` (venv and config).
3. Optionally, delete your parts library.

## Development
```bash
PY="$(cygpath -u "$LOCALAPPDATA")/print3d/venv/Scripts/python.exe"
"$PY" -m pip install -r requirements-dev.txt
"$PY" -m pytest
```

## License
MIT
