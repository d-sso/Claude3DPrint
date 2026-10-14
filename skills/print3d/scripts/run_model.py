"""print3d runner: load a parametric build123d model, apply overrides, build, export, report.

build123d is imported lazily (inside functions) so that parameter handling stays fast and testable.
"""
from __future__ import annotations

import ast
import contextlib
import copy
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import math
import os
import re
import sys
import time
import traceback
from pathlib import Path

import print3d_profile

LINEAR_TOL = 0.01  # mm, STL/3MF tessellation tolerance
ANGULAR_TOL = 0.1  # rad
ALLOWED_FORMATS = ("stl", "3mf", "step")
ALLOWED_TYPES = (bool, int, float, str)


class RunError(Exception):
    """A failure at a known stage: usage, load, params, build or export (unchanged)."""

    def __init__(self, stage, message, *, exc=None, line=None):
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.exc = exc
        self.line = line


def parse_overrides(pairs, flag="--set"):
    """Turn ["key=value", ...] into {"key": "value"}; values stay raw text."""
    raw = {}
    for item in pairs:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise RunError("usage", f"{flag} expects key=value, got {item!r}")
        raw[key] = value.strip()
    return raw


def coerce(text, default):
    """Convert override text to the type of the parameter's default value."""
    if isinstance(default, str):
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text
        return value if isinstance(value, str) else text
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        raise ValueError(f"cannot parse {text!r} as {type(default).__name__}") from None
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"value must be finite, got {text!r}")
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
    elif isinstance(default, int):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif isinstance(default, float):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    raise ValueError(f"expected {type(default).__name__}, got {type(value).__name__} ({text!r})")


def describe_parameters(defaults):
    return ", ".join(f"{key} ({type(value).__name__}, default {value!r})" for key, value in defaults.items())


def merge_parameters(defaults, raw):
    """Return (params, overrides): defaults merged with type-checked overrides."""
    unknown = sorted(set(raw) - set(defaults))
    if unknown:
        raise RunError(
            "params",
            f"Unknown parameter(s): {', '.join(unknown)}. Valid parameters: {describe_parameters(defaults)}",
        )
    params = dict(defaults)
    overrides = {}
    for key, text in raw.items():
        try:
            value = coerce(text, defaults[key])
        except ValueError as exc:
            raise RunError("params", f"Parameter '{key}': {exc}", exc=exc) from exc
        params[key] = value
        overrides[key] = value
    return params, overrides


PART_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def load_model(path):
    """Import a model file as a fresh module and validate its contract."""
    path = Path(path)
    module_name = "print3d_model_" + re.sub(r"\W", "_", path.stem)
    spec = importlib.util.spec_from_file_location(module_name, path, loader=SourceFileLoader(module_name, str(path)))
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SyntaxError as exc:
        raise RunError("load", f"SyntaxError: {exc.msg}", exc=exc, line=exc.lineno) from exc
    except (Exception, SystemExit) as exc:
        raise RunError("load", f"{type(exc).__name__}: {exc}", exc=exc) from exc

    params = getattr(module, "PARAMETERS", None)
    if not isinstance(params, dict):
        raise RunError("load", "Model must define PARAMETERS as a dict of name -> default value")
    for key, value in params.items():
        if not isinstance(key, str) or not isinstance(value, ALLOWED_TYPES):
            raise RunError(
                "load",
                f"PARAMETERS[{key!r}] must be int, float, bool or str (got {type(value).__name__})",
            )
    if not callable(getattr(module, "build", None)):
        raise RunError("load", "Model must define a function build(p) that returns the part(s)")
    if not isinstance(getattr(module, "CHECKS", {}), dict):
        raise RunError("load", "CHECKS must be a dict of check threshold -> value, e.g. {'min_wall': 0.6}")
    if hasattr(module, "expected") and not callable(module.expected):
        raise RunError("load", "expected must be a function expected(p) returning {part name: (x, y, z)}")
    return module


def normalize_parts(built):
    """Return {name: shape} from whatever build() returned, or raise a build-stage RunError."""
    from build123d import Compound, Part, Solid

    shape_types = (Part, Solid, Compound)
    if isinstance(built, shape_types):
        return {"main": built}
    if isinstance(built, dict):
        if not built:
            raise RunError("build", "build() returned an empty dict; return at least one part")
        for name, shape in built.items():
            if not isinstance(name, str) or not PART_NAME_RE.match(name):
                raise RunError(
                    "build",
                    f"Part name {name!r} is invalid; use lowercase letters, digits, '-' or '_' (e.g. 'lid')",
                )
            if not isinstance(shape, shape_types):
                raise RunError("build", f"Part {name!r} is a {type(shape).__name__}, not a build123d Part/Solid/Compound")
        return dict(built)
    hint = " (did you mean `return part.part`?)" if type(built).__name__.startswith("Build") else ""
    raise RunError(
        "build",
        f"build() must return a build123d Part, Solid or Compound, or a dict of name -> shape; "
        f"got {type(built).__name__}{hint}",
    )


def _r3(value):
    return round(float(value), 3)


def summarize(name, shape):
    """Volume, bounding box and validity for one part."""
    volume = float(shape.volume)
    if volume <= 0:
        raise RunError(
            "build",
            f"Part {name!r} has zero or negative volume ({volume:.3f} mm^3); the shape is empty or inside out",
        )
    box = shape.bounding_box()
    valid = shape.is_valid
    valid = valid() if callable(valid) else valid
    return {
        "name": name,
        "volume_mm3": _r3(volume),
        "bbox_mm": {
            "min": [_r3(box.min.X), _r3(box.min.Y), _r3(box.min.Z)],
            "max": [_r3(box.max.X), _r3(box.max.Y), _r3(box.max.Z)],
            "size": [_r3(box.size.X), _r3(box.size.Y), _r3(box.size.Z)],
        },
        "valid": bool(valid),
        "files": {},
    }


def _write_stl(shape, path):
    from build123d import export_stl

    return export_stl(shape, str(path), tolerance=LINEAR_TOL, angular_tolerance=ANGULAR_TOL)


def _write_step(shape, path):
    from build123d import export_step

    return export_step(shape, str(path))


def _write_3mf(parts, path):
    from build123d import Mesher

    mesher = Mesher()
    for name, shape in parts.items():
        named = copy.copy(shape)  # shallow copy so the caller's shape keeps its own label
        named.label = name
        mesher.add_shape(named, linear_deflection=LINEAR_TOL, angular_deflection=ANGULAR_TOL, part_number=name)
    mesher.write(str(path))
    return True


def _export_one(writer, payload, path):
    """Write one file; turn locks, OS errors and silent writer failures into export-stage RunErrors."""
    locked_hint = "If it is open in a slicer, close it there or use --out to write elsewhere."
    try:
        with open(path, "ab"):
            pass  # on Windows this raises PermissionError when another program holds the file
        ok = writer(payload, path)
    except PermissionError as exc:
        raise RunError("export", f"Cannot write {path.as_posix()}: file is locked or read-only. {locked_hint}", exc=exc) from exc
    except OSError as exc:
        raise RunError("export", f"Cannot write {path.as_posix()}: {exc}", exc=exc) from exc
    if ok is False:
        raise RunError("export", f"Writer failed for {path.as_posix()}. {locked_hint}")
    return path.resolve().as_posix()


def export_parts(parts, stem, out_dir, formats, result):
    """Export each part (STL/STEP) and one combined 3MF; record files in result as they are written."""
    single = len(parts) == 1
    for entry in result["parts"]:
        name = entry["name"]
        base = stem if single else f"{stem}-{name}"
        for fmt, writer in (("stl", _write_stl), ("step", _write_step)):
            if fmt in formats:
                entry["files"][fmt] = _export_one(writer, parts[name], out_dir / f"{base}.{fmt}")
    if "3mf" in formats:
        result["files"]["3mf"] = _export_one(_write_3mf, parts, out_dir / f"{stem}.3mf")


def _same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _model_frames(exc, model_path):
    frames = traceback.extract_tb(exc.__traceback__) if exc is not None else []
    return [f for f in frames if _same_file(f.filename, model_path)]


def error_info(err, model_path):
    """Shorten an expected failure to the model's own frames."""
    exc = err.exc
    frames = _model_frames(exc, model_path)
    line = err.line if err.line is not None else (frames[-1].lineno if frames else None)
    return {
        "stage": err.stage,
        "type": type(exc).__name__ if exc is not None else "RunError",
        "message": err.message,
        "line": line,
        "traceback": [
            f"{Path(f.filename).name}:{f.lineno} in {f.name}: {(f.line or '').strip()}" for f in frames
        ][-10:],
    }


def internal_error(stage, exc):
    """Unexpected failure inside the runner itself: keep the full traceback for debugging."""
    return {
        "stage": stage,
        "type": type(exc).__name__,
        "message": f"Internal runner error: {exc}",
        "line": None,
        "traceback": "".join(traceback.format_exception(exc)).splitlines(),
    }


def check_usage(model_path, formats):
    """Reject a missing or non-.py model, and unknown formats (formats=None when nothing is exported)."""
    if not model_path.is_file():
        raise RunError("usage", f"Model file not found: {model_path.as_posix()}")
    if model_path.suffix.lower() != ".py":
        raise RunError("usage", f"Model file must be a .py file: {model_path.as_posix()}")
    if formats is not None:
        bad = [f for f in formats if f not in ALLOWED_FORMATS]
        if bad or not formats:
            raise RunError("usage", f"Unknown format(s) {bad or formats}; choose from {', '.join(ALLOWED_FORMATS)}")


def activate_profile(profile):
    """Activate the given profile, or load and activate the default base profile. Returns the active profile."""
    if profile is None:
        try:
            profile = print3d_profile.load_profile()
        except print3d_profile.ProfileError as exc:
            raise RunError("usage", str(exc), exc=exc) from exc
    print3d_profile.activate(profile)
    return profile


def profile_record(profile):
    return {
        "name": profile["name"],
        "file": profile["file"],
        "overrides": dict(profile["overrides"]),
        "used": print3d_profile.used(),
    }


def stage_load(model_path):
    with contextlib.redirect_stdout(sys.stderr):
        return load_model(model_path)


def stage_params(module, raw, result):
    params, overrides = merge_parameters(module.PARAMETERS, raw)
    result["parameters"], result["overrides"] = params, overrides
    return params


def stage_build(module, params, result):
    with contextlib.redirect_stdout(sys.stderr):
        try:
            built = module.build(dict(params))
        except (Exception, SystemExit) as exc:
            raise RunError("build", f"{type(exc).__name__}: {exc}", exc=exc) from exc
        parts = normalize_parts(built)
        for name, shape in parts.items():
            summary = summarize(name, shape)
            result["parts"].append(summary)
            if not summary["valid"]:
                result["warnings"].append(f"Part {name!r} is not a valid solid according to build123d")
    return parts


def stage_export(parts, stem, out, formats, result):
    out.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(sys.stderr):
        export_parts(parts, stem, out, formats, result)


def resolve_checks(module, config_checks, raw_checks, profile):
    """Check thresholds: defaults < profile < config [checks] < model CHECKS < --check. Params-stage errors."""
    import check_model

    try:
        return check_model.resolve_thresholds(
            config_checks, getattr(module, "CHECKS", {}), raw_checks, profile=profile
        )
    except ValueError as exc:
        raise RunError("params", str(exc), exc=exc) from exc


def _is_size(value):
    return (
        isinstance(value, (tuple, list))
        and len(value) == 3
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)
    )


def _def_line(func):
    """Line where a model function is defined, for errors that have no model frame; None if unknown."""
    code = getattr(func, "__code__", None)
    return code.co_firstlineno if code is not None else None


def expected_sizes(module, params, parts):
    """Call the model's optional expected(p); returns {part name: (x, y, z)} or None when it isn't defined."""
    if not hasattr(module, "expected"):
        return None
    func = module.expected
    def_line = _def_line(func)
    with contextlib.redirect_stdout(sys.stderr):
        try:
            sizes = func(dict(params))
        except (Exception, SystemExit) as exc:
            in_model = bool(_model_frames(exc, getattr(module, "__file__", "")))
            raise RunError(
                "check", f"expected(): {type(exc).__name__}: {exc}", exc=exc, line=None if in_model else def_line
            ) from exc
    example = 'e.g. return {"main": (p["length"], p["width"], p["height"])}'
    if not isinstance(sizes, dict):
        raise RunError(
            "check", f"expected() must return a dict of part name -> (x, y, z) in mm, got {type(sizes).__name__}; {example}",
            line=def_line,
        )
    unknown = sorted(set(sizes) - set(parts))
    if unknown:
        raise RunError(
            "check", f"expected() names unknown part(s) {', '.join(map(repr, unknown))}; parts are {', '.join(map(repr, parts))}",
            line=def_line,
        )
    for name, size in sizes.items():
        if not _is_size(size):
            raise RunError("check", f"expected()[{name!r}] must be (x, y, z) in mm, got {size!r}; {example}", line=def_line)
    return sizes


def stage_check(module, params, parts, thresholds, sources, result):
    import check_model

    sizes = expected_sizes(module, params, parts)
    reports = {}
    try:
        for name, shape in parts.items():
            mesh = check_model.mesh_from_shape(shape)
            reports[name] = check_model.check_part(mesh, thresholds, None if sizes is None else sizes.get(name))
    except ImportError as exc:  # e.g. rtree missing because setup.ps1 wasn't re-run after an update
        missing = exc.name or str(exc)
        raise RunError(
            "check",
            f"Printability checks need the Python package {missing!r}, which is not installed. "
            "Re-run skills/print3d/scripts/setup.ps1 to update the print3d environment, or use --no-check.",
            exc=exc,
        ) from exc
    result["checks"] = {
        "status": check_model.worst_status(report["status"] for report in reports.values()),
        "thresholds": thresholds,
        "sources": sources,
        "parts": reports,
    }


def _timed(result, name, fn, *args):
    start = time.perf_counter()
    value = fn(*args)
    result["timings_s"][name] = _r3(time.perf_counter() - start)
    return value


def exit_code(result, strict):
    if not result["ok"]:
        return 2 if result["error"]["stage"] == "usage" else 1
    if strict and result["checks"] is not None and result["checks"]["status"] == "fail":
        return 3
    return 0


def run(
    model_path,
    set_pairs=(),
    formats=("stl", "3mf"),
    out_dir=None,
    profile=None,
    *,
    check=True,
    check_pairs=(),
    config_checks=None,
    strict=False,
    export=True,
):
    """Load, parameterise, build, export and check one model. Returns (result, exit_code).

    `profile` is a print3d_profile.load_profile() dict; None loads the default base profile.
    export=False (print3d check) writes nothing to disk. check=False (--no-check) leaves result["checks"] as None.
    """
    model_path = Path(model_path).resolve()
    result = {
        "ok": False,
        "model": model_path.as_posix(),
        "parameters": {},
        "overrides": {},
        "parts": [],
        "files": {},
        "checks": None,
        "warnings": [],
        "timings_s": {},
        "profile": None,
        "error": None,
    }
    out = Path(out_dir).resolve() if out_dir else model_path.parent / "out"
    stage = "usage"
    try:
        check_usage(model_path, formats if export else None)
        raw = parse_overrides(set_pairs)
        raw_checks = parse_overrides(check_pairs, "--check")
        profile = activate_profile(profile)

        stage = "load"
        module = _timed(result, "load", stage_load, model_path)

        stage = "params"
        params = stage_params(module, raw, result)
        if check:
            thresholds, sources = resolve_checks(module, config_checks, raw_checks, profile)

        stage = "build"
        parts = _timed(result, "build", stage_build, module, params, result)

        if export:
            stage = "export"
            _timed(result, "export", stage_export, parts, model_path.stem, out, formats, result)

        if check:
            stage = "check"
            _timed(result, "check", stage_check, module, params, parts, thresholds, sources, result)
        result["ok"] = True
    except RunError as err:
        result["error"] = error_info(err, model_path)
    except Exception as exc:
        result["error"] = internal_error(stage, exc)

    if stage != "usage" and profile is not None:
        result["profile"] = profile_record(profile)

    code = exit_code(result, strict)
    if export and code != 2:
        try:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{model_path.stem}.result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        except OSError as exc:
            result["warnings"].append(f"Could not write result.json: {exc}")
    return result, code
