"""Blender-side scene script for `print3d render`.

Runs inside Blender's own Python, so module level imports only the standard library. `bpy` and
`mathutils` are imported inside functions; never import build123d or other print3d modules here.
This part holds the pure helpers: the still list, camera directions and distance, colour
conversion and argument parsing.
"""
import argparse
import itertools
import math

VIEWS = ("iso", "front", "right", "top", "tools")
LIGHTINGS = ("studio", "hard", "dim")
STATES = ("assembled", "exploded", "tools")

DEFAULT_STILLS = (
    ("assembled", "iso", "studio"),
    ("assembled", "iso", "hard"),
    ("assembled", "iso", "dim"),
    ("assembled", "front", "studio"),
    ("assembled", "right", "studio"),
    ("assembled", "top", "studio"),
    ("exploded", "iso", "studio"),
    ("exploded", "front", "studio"),
    ("tools", "tools", "studio"),
)

QUALITY = {
    "draft": {"engine": "eevee", "resolution": (1280, 960), "samples": 32},
    "final": {"engine": "cycles", "resolution": (1920, 1440), "samples": 256},
}


def still_plan(views, lightings, states, *, has_tools, has_explode):
    """The (state, view, lighting) stills to render; empty axes fall back to defaults."""
    if not (views or lightings or states):
        plan = list(DEFAULT_STILLS)
    else:
        plan = list(itertools.product(states or ("assembled",), views or ("iso",),
                                      lightings or ("studio",)))
    return [s for s in plan
            if (has_tools or s[0] != "tools") and (has_explode or s[0] != "exploded")]


def still_name(state, view, lighting):
    return f"{state}-{view}-{lighting}.png"


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def view_direction(view, backdrop, facing):
    """(dir, up): dir is the unit vector from the target to the camera, up the image's up vector."""
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}")
    if backdrop != "wall":
        return {
            "front": ((0, -1, 0), (0, 0, 1)),
            "right": ((1, 0, 0), (0, 0, 1)),
            "top": ((0, 0, 1), (0, 1, 0)),
        }.get(view, (_norm((1, -1, 0.8)), (0, 0, 1)))
    face = _norm(facing)
    up = (0, 1, 0) if abs(face[2]) > 0.999 else (0, 0, 1)
    right = _cross(up, face)  # facing turned 90 degrees about +Z
    if view == "front":
        return face, up
    if view == "right":
        return right, up
    if view == "top":
        return up, tuple(-c for c in face)
    return _norm(tuple(f + 0.6 * r + 0.5 * u for f, r, u in zip(face, right, up))), up


def fit_distance(radius, lens_mm=50.0, sensor_mm=36.0, aspect=4 / 3, margin=1.1):
    """Camera distance at which a sphere of `radius` fills the narrower field of view, times margin."""
    narrow = sensor_mm / 2 * min(1.0, 1.0 / aspect)  # sensor_mm is the width
    return radius / math.sin(math.atan(narrow / lens_mm)) * margin


def srgb_to_linear(rgb255):
    """sRGB 0-255 triple to linear 0-1 (IEC 61966-2-1 transfer)."""
    out = []
    for c in rgb255:
        c = c / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def _still_spec(text):
    left, sep, path = text.partition("=")
    parts = left.split(":")
    if not sep or len(parts) != 3 or not path:
        raise argparse.ArgumentTypeError(f"expected STATE:VIEW:LIGHTING=PATH, got {text!r}")
    return (*parts, path)


def _resolution(text):
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WxH, got {text!r}") from None


def parse_args(argv):
    """Parse the arguments after `--` on Blender's command line."""
    p = argparse.ArgumentParser(prog="blender_scene")
    p.add_argument("scene_json")
    p.add_argument("--blend")
    p.add_argument("--still", action="append", default=[], type=_still_spec)
    p.add_argument("--quality", choices=sorted(QUALITY), default="draft")
    p.add_argument("--resolution", type=_resolution)
    p.add_argument("--samples", type=int)
    return p.parse_args(argv)
