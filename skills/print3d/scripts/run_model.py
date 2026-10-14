"""print3d runner: load a parametric build123d model, apply overrides, build, export, report.

build123d is imported lazily (inside functions) so that parameter handling stays fast and testable.
"""
from __future__ import annotations

import ast

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
