"""print3d render: stage each scene group (a model, unit or layout), run Blender on it with blender_scene.py, and
write out/render/<group folder>/ (scene.blend and <state>-<view>-<lighting>.png) and out/render.result.json.

Blender is found from the config key `blender`, then PRINT3D_BLENDER, then the default install folders (newest
first); candidates older than MIN_BLENDER are skipped. Exit codes: 0 ok, 1 error (Blender missing or failing
included), 2 usage.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import blender_scene
import run_model
import scene_model
from run_model import RunError

SCRIPT = Path(__file__).resolve().parent / "blender_scene.py"
MIN_BLENDER = (4, 2)
BLENDER_TIMEOUT_S = 3600  # per group
VERSION_TIMEOUT_S = 60
RESULT_FILE = "render.result.json"
RESULT_PREFIX = "PRINT3D_RESULT "
LOG_LINES = 20
MISSING_MESSAGE = ("Blender 4.2+ not found. Install it (winget install BlenderFoundation.Blender, or blender.org), "
                   "then re-run setup.ps1.")
FOLDER_VERSION_RE = re.compile(r"^Blender (\d+)\.(\d+)$", re.IGNORECASE)
OUTPUT_VERSION_RE = re.compile(r"^Blender (\d+)\.(\d+)")
RESOLUTION_RE = re.compile(r"^(\d+)x(\d+)$")


# --- finding Blender ----------------------------------------------------------------------------------------------


def blender_version(path) -> tuple | None:
    """(major, minor) from the folder name `Blender X.Y`, else from `blender --version`; None when unknown."""
    path = Path(path)
    match = FOLDER_VERSION_RE.match(path.parent.name)
    if match:
        return int(match[1]), int(match[2])
    try:
        proc = subprocess.run([str(path), "--version"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=VERSION_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = proc.stdout.strip().splitlines()
    match = OUTPUT_VERSION_RE.match(lines[0].strip()) if lines else None
    return (int(match[1]), int(match[2])) if match else None


def _installed():
    """The default install folders' blender.exe files, newest version first (unknown versions last)."""
    root = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
    found = []
    for exe in root.glob("Blender */blender.exe"):
        match = FOLDER_VERSION_RE.match(exe.parent.name)
        found.append(((int(match[1]), int(match[2])) if match else (-1, -1), exe))
    return [exe for _, exe in sorted(found, key=lambda pair: pair[0], reverse=True)]


def find_blender(config: dict) -> Path | None:
    """The first Blender of at least MIN_BLENDER: config `blender`, then PRINT3D_BLENDER, then the install scan."""
    candidates = [config.get("blender"), os.environ.get("PRINT3D_BLENDER")]
    candidates = [Path(c) for c in candidates if c] + _installed()
    for path in candidates:
        if not path.is_file():
            continue
        version = blender_version(path)
        if version is not None and version >= MIN_BLENDER:
            return path
    return None


def blender_command(blender: Path) -> list[str]:
    return [str(blender), "--background", "--factory-startup", "--python", str(SCRIPT)]


def parse_result(stdout) -> dict | None:
    """The last `PRINT3D_RESULT <json>` line's JSON (Blender's own banner can follow it); None when absent."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                value = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


# --- one group ----------------------------------------------------------------------------------------------------


def parse_resolution(value):
    """`WxH` (or a (w, h) pair) to a positive (w, h); a usage error otherwise."""
    if isinstance(value, str):
        match = RESOLUTION_RE.match(value)
        if not match:
            raise RunError("usage", f"--resolution must be WxH, e.g. 1280x960; got {value!r}")
        value = (int(match[1]), int(match[2]))
    w, h = value
    if w < 1 or h < 1:
        raise RunError("usage", f"--resolution must be at least 1x1; got {w}x{h}")
    return int(w), int(h)


def _unique(values):
    return tuple(dict.fromkeys(values))


def clear_group(folder):
    """Remove the previous render of a group: its PNGs and .blend files, nothing else."""
    for old in [*folder.glob("*.png"), folder / "scene.blend", folder / "scene.blend1"]:
        if old.is_file():
            old.unlink()


def _tail(text):
    lines = (text or "").rstrip().splitlines()[-LOG_LINES:]
    return "\n".join(lines) if lines else "(empty)"


def _blender_error(group, reason, proc_stdout, proc_stderr):
    return RunError(
        "blender",
        f"Blender failed on {group.folder}: {reason}\n"
        f"--- stderr (last {LOG_LINES} lines) ---\n{_tail(proc_stderr)}\n"
        f"--- stdout (last {LOG_LINES} lines) ---\n{_tail(proc_stdout)}",
    )


def _run_blender(group, cmd):
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=BLENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr
        raise _blender_error(group, f"timed out after {BLENDER_TIMEOUT_S} s", out, err) from exc
    except OSError as exc:
        raise RunError("blender", f"Could not start Blender ({cmd[0]}): {exc}", exc=exc) from exc
    res = parse_result(proc.stdout)
    if proc.returncode != 0:
        raise _blender_error(group, f"exit code {proc.returncode}", proc.stdout, proc.stderr)
    if res is None:
        raise _blender_error(group, "no PRINT3D_RESULT line on stdout", proc.stdout, proc.stderr)
    if not res.get("ok"):
        reason = (res.get("error") or "ok: false").strip().splitlines()[-1]
        raise _blender_error(group, f"Blender reported an error: {reason}", proc.stdout, proc.stderr)
    return res


def render_group(group, root, blender, plan, quality, resolution, samples, timings):
    """Stage and render one group into root/<folder>/; returns (entry, blender result or None)."""
    folder = root / group.folder
    entry = {"kind": group.kind, "name": group.name, "folder": group.folder, "blend": None, "images": [],
             "tool_access": group.tool_access, "warnings": list(group.warnings)}
    if not any(o.collection == "tools" for o in group.objects):
        entry["warnings"].append(f"{group.folder}: no screws, so no tools view")
    folder.mkdir(parents=True, exist_ok=True)
    clear_group(folder)
    if not plan:
        entry["warnings"].append(f"{group.folder}: none of the requested stills apply (no tools or exploded state)")
        return entry, None

    start = time.perf_counter()
    staging = folder / "_staging"
    scene_json = scene_model.write_staging(group, staging)
    timings["staging"] = run_model._r3(timings.get("staging", 0.0) + time.perf_counter() - start)

    blend = folder / "scene.blend"
    args = ["--", str(scene_json), "--blend", str(blend), "--quality", quality,
            "--resolution", f"{resolution[0]}x{resolution[1]}", "--samples", str(samples)]
    for state, view, lighting in plan:
        args += ["--still", f"{state}:{view}:{lighting}={folder / blender_scene.still_name(state, view, lighting)}"]
    start = time.perf_counter()
    try:
        res = _run_blender(group, blender_command(blender) + args)
    finally:
        timings["blender"] = run_model._r3(timings.get("blender", 0.0) + time.perf_counter() - start)
    images = [{"state": s["state"], "view": s["view"], "lighting": s["lighting"],
               "path": Path(s["path"]).resolve().as_posix()} for s in res.get("stills", [])]
    missing = [i["path"] for i in images if not Path(i["path"]).is_file()] + ([] if blend.is_file() else [str(blend)])
    if missing or len(images) != len(plan):
        raise RunError("blender", f"Blender reported success on {group.folder} but did not write "
                                  f"{', '.join(missing) or f'{len(plan)} stills (got {len(images)})'}")
    entry["images"], entry["blend"] = images, blend.resolve().as_posix()
    shutil.rmtree(staging, ignore_errors=True)
    return entry, res


# --- the command --------------------------------------------------------------------------------------------------


def _exit_code(result):
    if result["ok"]:
        return 0
    return 2 if result["error"]["stage"] == "usage" else 1


def render(path, set_pairs=(), level="all", unit=None, layout=None, quality="draft", views=(), lightings=(),
           states=(), resolution=None, out_dir=None, profile=None, open_gui=False, blender=None, config=None):
    """Render every selected group with Blender. Returns (result, exit code).

    Each group's previous PNGs and scene.blend are removed first (also when the run then fails), so none sits next
    to a newer result. Its staging folder (<folder>/_staging) is removed after a successful render and kept after a
    failed one, for debugging. blender: the executable (None: find_blender(config), config being the loaded
    config.toml). open_gui: launch Blender on the first .blend after a successful run.
    """
    path = Path(path).resolve()
    result = {
        "ok": False,
        "model": path.as_posix(),
        "parameters": {},
        "overrides": {},
        "shared": {},
        "profile": None,
        "quality": None,
        "blender": {"path": None, "version": None},
        "groups": [],
        "warnings": [],
        "timings_s": {},
        "error": None,
    }
    out = Path(out_dir).resolve() if out_dir else path.parent / "out"
    stage, error_model = "usage", path
    try:
        if quality not in blender_scene.QUALITY:
            raise RunError("usage", f"quality must be one of {', '.join(blender_scene.QUALITY)}; got {quality!r}")
        q = blender_scene.QUALITY[quality]
        size = parse_resolution(resolution) if resolution is not None else tuple(q["resolution"])
        result["quality"] = {"name": quality, "engine": None, "device": None, "resolution": list(size),
                             "samples": q["samples"]}
        views, lightings, states = _unique(views), _unique(lightings), _unique(states)
        profile = run_model.activate_profile(profile)

        stage = "blender"
        if blender is None:
            blender = find_blender(config or {})
        if blender is None:
            raise RunError("blender", MISSING_MESSAGE)
        result["blender"]["path"] = Path(blender).resolve().as_posix()

        stage = "load"
        start = time.perf_counter()
        try:
            groups, meta = scene_model.load_groups(path, set_pairs, level, unit, layout, profile)
        except RunError as err:
            error_model = Path(getattr(err, "model", path))
            raise
        result["timings_s"]["load"] = run_model._r3(time.perf_counter() - start)
        for key in ("parameters", "overrides", "shared"):
            result[key] = meta[key]
        result["warnings"] += meta["warnings"]

        stage = "blender"
        root = out / "render"
        for group in groups:
            has_tools = any(o.collection == "tools" for o in group.objects)
            has_explode = any(any(o.explode) for o in group.objects if o.collection != "tools")
            plan = blender_scene.still_plan(views, lightings, states, has_tools=has_tools, has_explode=has_explode)
            entry, res = render_group(group, root, blender, plan, quality, size, q["samples"], result["timings_s"])
            result["groups"].append(entry)
            result["warnings"] += [w for w in entry["warnings"] if w not in result["warnings"]]
            if res is not None:
                result["blender"]["version"] = res.get("blender_version")
                result["quality"]["engine"], result["quality"]["device"] = res.get("engine"), res.get("device")
        result["ok"] = True
    except RunError as err:
        result["error"] = run_model.error_info(err, error_model)
    except Exception as exc:
        result["error"] = run_model.internal_error(stage, exc)

    if stage != "usage" and profile is not None:
        result["profile"] = run_model.profile_record(profile)

    blends = [g["blend"] for g in result["groups"] if g["blend"]]
    if open_gui and result["ok"] and blends:
        try:
            subprocess.Popen([str(blender), str(Path(blends[0]))])
        except OSError as exc:
            result["warnings"].append(f"Could not open Blender: {exc}")

    code = _exit_code(result)
    if code != 2:
        try:
            out.mkdir(parents=True, exist_ok=True)
            (out / RESULT_FILE).write_text(json.dumps(result, indent=2), encoding="utf-8")
        except OSError as exc:
            result["warnings"].append(f"Could not write {RESULT_FILE}: {exc}")

    return result, code
