"""Shared values for part models inside an assembly project.

A project is a folder with assembly.py; interfaces.py next to it holds INTERFACES, a dict of values that
several parts must agree on. Part models read them with shared("wall", ...). run_model activates the project
around loading a model, and records which names it asked for.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import traceback
from importlib.machinery import SourceFileLoader
from pathlib import Path

_state = None  # {"root": Path | None, "overrides": dict, "values": dict | None, "requested": dict}


def find_project_root(model_path):
    """Nearest ancestor (including the model's own folder) that contains assembly.py, or None."""
    here = Path(model_path).resolve().parent
    for folder in (here, *here.parents):
        if (folder / "assembly.py").is_file():
            return folder
    return None


def load_interfaces(root):
    """Execute root/interfaces.py and return its validated INTERFACES dict."""
    from run_model import ALLOWED_TYPES

    path = Path(root) / "interfaces.py"
    if not path.is_file():
        raise ValueError(f"interfaces.py not found in {Path(root).as_posix()}")
    spec = importlib.util.spec_from_file_location(
        "print3d_interfaces", path, loader=SourceFileLoader("print3d_interfaces", str(path))
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SyntaxError as exc:
        raise ValueError(f"interfaces.py:{exc.lineno}: SyntaxError: {exc.msg}") from exc
    except Exception as exc:
        line = None
        for frame in traceback.extract_tb(exc.__traceback__):
            if os.path.normcase(os.path.abspath(frame.filename)) == os.path.normcase(os.path.abspath(path)):
                line = frame.lineno
        where = f"interfaces.py:{line}" if line else "interfaces.py"
        raise ValueError(f"{where}: {type(exc).__name__}: {exc}") from exc
    interfaces = getattr(module, "INTERFACES", None)
    if not isinstance(interfaces, dict):
        raise ValueError("interfaces.py must define INTERFACES as a dict of name -> value")
    for key, value in interfaces.items():
        if not isinstance(key, str) or not isinstance(value, ALLOWED_TYPES):
            raise ValueError(f"INTERFACES[{key!r}] must be int, float, bool or str (got {type(value).__name__})")
    return interfaces


@contextlib.contextmanager
def activate(root, overrides=None):
    """Make `root` the active project for shared(); restores the previous state on exit."""
    global _state
    previous = _state
    _state = {
        "root": None if root is None else Path(root),
        "overrides": dict(overrides or {}),
        "values": None,
        "requested": {},
    }
    try:
        yield
    finally:
        _state = previous


def requested():
    """The names (and values) shared() handed out under the active project."""
    return {} if _state is None else dict(_state["requested"])


def shared(*names):
    """Values of `names` from the active project's INTERFACES, with overrides applied."""
    if _state is None or _state["root"] is None:
        raise RuntimeError(
            "shared() needs a project: no assembly.py found in this model's folder or any folder above it"
        )
    if _state["values"] is None:
        _state["values"] = {**load_interfaces(_state["root"]), **_state["overrides"]}
    values = _state["values"]
    unknown = [name for name in names if name not in values]
    if unknown:
        raise KeyError(f"Unknown shared value(s) {', '.join(map(repr, unknown))}. Valid names: {', '.join(values)}")
    picked = {name: values[name] for name in names}
    _state["requested"].update(picked)
    return picked
