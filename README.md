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
volume and any errors.

## Model files
```python
from build123d import *

PARAMETERS = {"length": 40.0, "width": 30.0, "height": 15.0}  # mm; override with --set key=value

def build(p):
    with BuildPart() as part:
        Box(p["length"], p["width"], p["height"], align=(Align.CENTER, Align.CENTER, Align.MIN))
    return part.part  # or {"lid": ..., "base": ...} for several parts
```

## Uninstall
1. Delete the link `~/.claude/skills/print3d`. This only removes the junction, not the repo.
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
