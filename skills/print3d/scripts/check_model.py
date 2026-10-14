"""print3d printability checks: analyse each part's tessellated mesh and report what would print badly.

Pure analysis: no file I/O, and build123d is never imported (shapes are only asked to tessellate themselves).
"""
from __future__ import annotations

import numpy as np
import trimesh

from run_model import ANGULAR_TOL, LINEAR_TOL, coerce  # same tessellation and value parsing as the runner

INWARD_OFFSET = 1e-4  # mm, ray origins start just inside the surface
BED_CONTACT_TOL = 0.01  # mm, faces this close to the part's lowest Z sit on the bed
MAX_REGIONS = 10
SEED = 0

DEFAULTS = {
    "bed_x": 256.0,  # mm, build volume (Elegoo Centauri Carbon)
    "bed_y": 256.0,
    "bed_z": 256.0,
    "bed_margin": 5.0,  # mm, keep-out at each bed edge
    "min_wall": 0.8,  # mm, 2 perimeters with a 0.4 mm nozzle
    "min_feature": 0.4,  # mm, 1 nozzle width
    "overhang_angle": 45.0,  # degrees past vertical
    "overhang_min_area": 1.0,  # mm^2, smaller overhang regions are ignored
    "size_tol": 0.1,  # mm, allowed difference from expected(p)
    "samples": 3000,  # area-weighted thickness samples per part (plus triangle centroids)
}
STATUS_ORDER = {"skipped": 0, "pass": 1, "warn": 2, "fail": 3}
CHECK_NAMES = ("sealed", "size", "bed_fit", "wall", "thin", "overhang")
# Thresholds the printer profile (#5) supplies: threshold -> (profile section, key).
PROFILE_KEYS = {
    "bed_x": ("printer", "bed_x"),
    "bed_y": ("printer", "bed_y"),
    "bed_z": ("printer", "bed_z"),
    "bed_margin": ("checks", "bed_margin"),
    "min_wall": ("checks", "min_wall"),
    "min_feature": ("checks", "min_feature"),
    "overhang_angle": ("checks", "overhang_angle"),
}


def describe_thresholds():
    return ", ".join(f"{key} (default {value!r})" for key, value in DEFAULTS.items())


def _coerce_threshold(key, value, from_text):
    """Type-check one threshold against its default. CLI values arrive as text, config/model values typed."""
    default = DEFAULTS[key]
    try:
        if not from_text and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError(f"expected {type(default).__name__}, got {type(value).__name__} ({value!r})")
        result = coerce(value if from_text else repr(value), default)
    except ValueError as exc:
        raise ValueError(f"Check threshold '{key}': {exc}") from None
    if key == "samples" and result < 100:
        raise ValueError(f"Check threshold 'samples' must be at least 100, got {result}")
    if key != "samples" and result <= 0:
        raise ValueError(f"Check threshold '{key}' must be greater than 0, got {result}")
    return result


def profile_checks(profile):
    """The thresholds an active printer profile supplies; {} when there is no profile."""
    if not profile:
        return {}
    values = profile["values"]
    return {key: values[section][name] for key, (section, name) in PROFILE_KEYS.items()}


def resolve_thresholds(config_checks=None, model_checks=None, cli_checks=None, *, profile=None):
    """Merge defaults < profile < config [checks] < model CHECKS < CLI --check (raw text).

    Returns (thresholds, sources). Raises ValueError naming the bad key and listing the valid ones.
    """
    thresholds = dict(DEFAULTS)
    sources = {key: "default" for key in DEFAULTS}
    layers = (
        ("profile", profile_checks(profile), False),
        ("config", config_checks or {}, False),
        ("model", model_checks or {}, False),
        ("cli", cli_checks or {}, True),
    )
    for source, layer, from_text in layers:
        unknown = sorted(set(layer) - set(DEFAULTS))
        if unknown:
            raise ValueError(
                f"Unknown check threshold(s) in {source}: {', '.join(unknown)}. Valid: {describe_thresholds()}"
            )
        for key, value in layer.items():
            thresholds[key] = _coerce_threshold(key, value, from_text)
            sources[key] = source
    return thresholds, sources
