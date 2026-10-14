"""print3d runner: load a parametric build123d model, apply overrides, build, export, report.

build123d is imported lazily (inside functions) so that parameter handling stays fast and testable.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path

LINEAR_TOL = 0.01  # mm, STL/3MF tessellation tolerance
ANGULAR_TOL = 0.1  # rad
ALLOWED_FORMATS = ("stl", "3mf", "step")
ALLOWED_TYPES = (bool, int, float, str)


class RunError(Exception):
    """A failure at a known stage: usage, load, params, build or export."""

    def __init__(self, stage, message, *, exc=None, line=None):
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.exc = exc
        self.line = line


def parse_overrides(pairs):
    """Turn ["key=value", ...] into {"key": "value"}; values stay raw text."""
    raw = {}
    for item in pairs:
        key, sep, value = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise RunError("usage", f"--set expects key=value, got {item!r}")
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
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SyntaxError as exc:
        raise RunError("load", f"SyntaxError: {exc.msg}", exc=exc, line=exc.lineno) from exc
    except Exception as exc:
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
