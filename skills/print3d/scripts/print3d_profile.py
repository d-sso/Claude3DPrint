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


# ---- Model helpers -------------------------------------------------------------------------------------------

# ISO 273 "normal" clearance holes and ISO 4032 hex nut across-flats (AF) and height (m), in mm.
ISO273_NORMAL = {"M2": 2.4, "M2.5": 2.9, "M3": 3.4, "M4": 4.5, "M5": 5.5, "M6": 6.6, "M8": 9.0}
ISO4032_AF = {"M2": 4.0, "M2.5": 5.0, "M3": 5.5, "M4": 7.0, "M5": 8.0, "M6": 10.0, "M8": 13.0}
ISO4032_M = {"M2": 1.6, "M2.5": 2.0, "M3": 2.4, "M4": 3.2, "M5": 4.7, "M6": 5.2, "M8": 6.8}

_active = None
_used = {}


def activate(profile):
    """Make `profile` (a load_profile dict, or None for the base-profile fallback) active and clear the record."""
    global _active
    _active = profile
    _used.clear()


def used():
    """Every profile value handed out since activate(), as {dotted_key: value}."""
    return dict(_used)


def _profile():
    global _active
    if _active is None:
        _active = load_profile(DEFAULT_PROFILE)
    return _active


def _get(dotted):
    section, key = split_key(dotted)
    values = _profile()["values"]
    if section not in values:
        raise ValueError(f"Unknown profile section {section!r} in {dotted!r}; sections: {', '.join(values)}")
    if key not in values[section]:
        raise ValueError(f"Unknown profile key {dotted!r}; keys in [{section}]: {', '.join(values[section])}")
    value = values[section][key]
    _used[dotted] = value
    return value


def _choose(what, options, choice):
    if choice not in options:
        raise ValueError(f"Unknown {what} {choice!r}; choose from: {', '.join(options)}")


def _r3(value):
    return round(float(value), 3)


def fit(kind):
    """Diametral clearance (hole diameter - shaft diameter) for a printed-to-printed fit: press, snug, sliding, loose."""
    _choose("fit", list(_profile()["values"]["fits"]), kind)
    return _r3(_get(f"fits.{kind}"))


def hole(d):
    """CAD diameter for a vertical hole that should print as diameter `d`."""
    return _r3(float(d) + _get("holes.compensation"))


def screw_clearance(size):
    """Compensated ISO 273 normal clearance hole for a metric screw, e.g. 'M3'."""
    _choose("screw size", list(ISO273_NORMAL), size)
    return hole(ISO273_NORMAL[size])


def insert_hole(size):
    """Heat-set insert pilot hole diameter from the profile (not compensated)."""
    _choose("insert size", list(_profile()["values"]["inserts"]), size)
    return _r3(_get(f"inserts.{size}"))


def nut_pocket(size):
    """(across_flats, height) for a hex nut pocket: ISO 4032 nut size plus the profile's allowances."""
    _choose("nut size", list(ISO4032_AF), size)
    return (_r3(ISO4032_AF[size] + _get("nuts.af_clearance")), _r3(ISO4032_M[size] + _get("nuts.height_clearance")))


def profile_value(dotted):
    """Any value of the active profile by dotted key, e.g. 'printer.layer_height'."""
    return _r3(_get(dotted))
