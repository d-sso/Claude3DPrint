"""Printer profiles: loading, validation, and the helpers models use for printer-calibrated tolerances.

In a model:
    from print3d_profile import fit, hole, screw_clearance, insert_hole, nut_pocket, profile_value

A profile is one printer + nozzle + material. Base profiles live in skills/print3d/profiles/<name>.toml;
calibrated overrides live in the local config.toml under [profile_overrides.<name>].
This module never imports build123d or run_model.
"""
from __future__ import annotations

import ast
import math
import re
import tomllib
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
DEFAULT_PROFILE = "centauri-carbon-pla-plus"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
REQUIRED = {
    "printer": ("bed_x", "bed_y", "bed_z", "nozzle", "layer_height"),
    "holes": ("compensation",),
    "fits": ("press", "snug", "sliding", "loose"),
    "inserts": ("M2", "M2.5", "M3", "M4", "M5"),
    "nuts": ("af_clearance", "height_clearance"),
    "checks": ("bed_margin", "min_wall", "min_feature", "overhang_angle"),
}


class ProfileError(ValueError):
    """An invalid profile, profile name or override. The CLI reports it as a usage error."""


def available_profiles(profiles_dir=None):
    folder = Path(profiles_dir) if profiles_dir else PROFILES_DIR
    return sorted(p.stem for p in folder.glob("*.toml"))


def split_key(dotted):
    section, sep, key = str(dotted).partition(".")
    if not sep or not section or not key:
        raise ProfileError(f"Profile key {dotted!r} must look like 'section.key', e.g. 'holes.compensation'")
    return section, key


def _zero_allowed(section, key):
    return section == "fits" or (section, key) == ("holes", "compensation")


def _check_number(dotted, value, where):
    section, key = split_key(dotted)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"{where}: {dotted} must be a number, got {type(value).__name__} ({value!r})")
    value = float(value)
    if not math.isfinite(value):
        raise ProfileError(f"{where}: {dotted} must be finite, got {value!r}")
    if _zero_allowed(section, key):
        if value < 0:
            raise ProfileError(f"{where}: {dotted} must be >= 0, got {value!r}")
    elif value <= 0:
        raise ProfileError(f"{where}: {dotted} must be > 0, got {value!r}")
    return value


def _parse_override(dotted, raw):
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw.strip())
        except (ValueError, SyntaxError):
            raise ProfileError(f"override: {dotted} must be a number, got {raw!r}") from None
    return _check_number(dotted, raw, "override")


def load_profile(name=DEFAULT_PROFILE, overrides=None, profiles_dir=None):
    """Load profiles/<name>.toml, validate it, apply overrides ({dotted_key: value}) and return the profile dict."""
    folder = Path(profiles_dir) if profiles_dir else PROFILES_DIR
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ProfileError(f"Profile name {name!r} must be kebab-case, e.g. {DEFAULT_PROFILE!r}")
    path = folder / f"{name}.toml"
    if not path.is_file():
        names = ", ".join(available_profiles(folder)) or "none"
        raise ProfileError(f"Unknown profile {name!r}; available profiles: {names}")
    where = path.resolve().as_posix()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{where}: invalid TOML: {exc}") from exc

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ProfileError(f"{where}: missing 'title' (a short human-readable description)")
    values = {}
    for section, keys in REQUIRED.items():
        table = data.get(section)
        if not isinstance(table, dict):
            raise ProfileError(f"{where}: missing section [{section}]")
        for key in keys:
            if key not in table:
                raise ProfileError(f"{where}: missing key {section}.{key}")
        values[section] = {key: _check_number(f"{section}.{key}", value, where) for key, value in table.items()}

    applied = {}
    for dotted, raw in (overrides or {}).items():
        section, key = split_key(dotted)
        if section not in values or key not in values[section]:
            raise ProfileError(f"Unknown profile key {dotted!r} for profile {name!r}")
        value = _parse_override(dotted, raw)
        values[section][key] = value
        applied[dotted] = value
    return {"name": name, "file": where, "title": title, "values": values, "overrides": applied}
