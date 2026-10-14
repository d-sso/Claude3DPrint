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


def mesh_from_shape(shape):
    """Tessellate a build123d shape like the exporters do, minus the zero-area slivers left at fillet corners."""
    vertices, triangles = shape.tessellate(LINEAR_TOL, ANGULAR_TOL)
    mesh = trimesh.Trimesh(
        vertices=np.array([(v.X, v.Y, v.Z) for v in vertices], dtype=float).reshape(-1, 3),
        faces=np.array(triangles, dtype=np.int64).reshape(-1, 3),
        process=True,
    )
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    return mesh


def _r3(value):
    return round(float(value), 3)


def _vec(values):
    return [_r3(v) for v in values]


def _result(check, status, message, **fields):
    return {"check": check, "status": status, "message": message, **fields}


def check_sealed(mesh):
    problems = []
    if len(mesh.faces) == 0:
        problems.append("mesh has no faces")
    else:
        if not mesh.is_watertight:
            problems.append("mesh has open edges")
        if not mesh.is_winding_consistent:
            problems.append("face winding is inconsistent")
        if mesh.volume <= 0:
            problems.append("volume is not positive (inside out)")
    if problems:
        return _result(
            "sealed", "fail", "Not sealed: " + ", ".join(problems) + "; close the shape or remove zero-thickness faces"
        )
    return _result("sealed", "pass", "Mesh is watertight and consistently wound")


def check_size(mesh, expected_size, tol):
    if expected_size is None:
        return _result("size", "skipped", "No expected size for this part; add expected(p) to the model")
    measured = mesh.extents
    expected = np.array(expected_size, dtype=float)
    off = np.abs(measured - expected) > tol
    fields = {"measured": _vec(measured), "expected": _vec(expected), "limit": _r3(tol)}
    if off.any():
        axes = ", ".join(f"{axis} {measured[i]:.3f} vs {expected[i]:.3f}" for i, axis in enumerate("XYZ") if off[i])
        return _result("size", "fail", f"Size differs from expected by more than {tol} mm: {axes}", **fields)
    return _result("size", "pass", f"Size matches expected within {tol} mm", **fields)


def check_bed_fit(mesh, t):
    limit = np.array([t["bed_x"] - 2 * t["bed_margin"], t["bed_y"] - 2 * t["bed_margin"], t["bed_z"]])
    measured = mesh.extents
    over = measured > limit
    fields = {"measured": _vec(measured), "limit": _vec(limit)}
    if over.any():
        axes = ", ".join(f"{axis} {measured[i]:.1f} > {limit[i]:.1f}" for i, axis in enumerate("XYZ") if over[i])
        return _result("bed_fit", "fail", f"Too big for the bed: {axes} mm; scale down, split or reorient", **fields)
    return _result("bed_fit", "pass", "Fits the bed", **fields)


def sample_thickness(mesh, samples):
    """Local thickness at surface points: inward ray along -normal to the first hit. Returns (points, thickness)."""
    points, face_index = trimesh.sample.sample_surface(mesh, samples, seed=SEED)
    # Add triangle centroids (evenly spaced, capped at `samples`) so small features on large parts are not missed.
    count = min(len(mesh.faces), samples)
    centroid_faces = np.unique(np.linspace(0, len(mesh.faces) - 1, count).astype(np.int64))
    points = np.vstack([points, mesh.triangles_center[centroid_faces]])
    normals = mesh.face_normals[np.concatenate([face_index, centroid_faces])]
    hits, ray_index, _ = mesh.ray.intersects_location(points - normals * INWARD_OFFSET, -normals, multiple_hits=False)
    thickness = np.full(len(points), np.nan)
    thickness[ray_index] = np.linalg.norm(hits - points[ray_index], axis=1)
    keep = np.isfinite(thickness)
    return points[keep], thickness[keep]


def _point_regions(points, values, radius):
    """Group points by single linkage within radius; each region is reported at its thinnest point."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    pairs = cKDTree(points).query_pairs(radius, output_type="ndarray")
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(len(points), len(points)))
    _, labels = connected_components(graph, directed=False)
    regions = []
    for label in np.unique(labels):
        idx = np.nonzero(labels == label)[0]
        worst = idx[np.argmin(values[idx])]
        regions.append({
            "center": _vec(points[worst]),
            "bbox_min": _vec(points[idx].min(axis=0)),
            "bbox_max": _vec(points[idx].max(axis=0)),
            "value": _r3(values[worst]),
            "samples": int(len(idx)),
        })
    regions.sort(key=lambda r: (r["value"], -r["samples"]))
    return regions


def check_thickness(name, points, thickness, limit, radius):
    """`wall` (limit min_wall) or `thin` (limit min_feature) from the shared thickness samples."""
    what, fix = ("Wall", "thicken it") if name == "wall" else ("Feature", "thicken or remove it")
    measured = _r3(thickness.min()) if len(thickness) else None
    bad = thickness < limit
    if not bad.any():
        return _result(name, "pass", f"No {what.lower()} thinner than {limit} mm", measured=measured, limit=_r3(limit))
    regions = _point_regions(points[bad], thickness[bad], radius)
    return _result(
        name, "fail", f"{what} {measured:.2f} mm < {limit} mm in {len(regions)} region(s); {fix}",
        measured=measured, limit=_r3(limit), regions=regions[:MAX_REGIONS],
    )


def overhang_faces(mesh, angle):
    """(indices of faces leaning more than `angle` degrees past vertical and not on the bed, per-face angle)."""
    past_vertical = 90.0 - np.degrees(np.arccos(np.clip(-mesh.face_normals[:, 2], -1.0, 1.0)))
    z = mesh.vertices[mesh.faces][:, :, 2]
    on_bed = np.all(z - mesh.bounds[0, 2] < BED_CONTACT_TOL, axis=1)
    return np.nonzero((past_vertical > angle + 1e-6) & ~on_bed)[0], past_vertical


def check_overhang(mesh, t):
    angle, min_area = t["overhang_angle"], t["overhang_min_area"]
    faces, past_vertical = overhang_faces(mesh, angle)
    regions = []
    if len(faces):
        selected = np.zeros(len(mesh.faces), dtype=bool)
        selected[faces] = True
        adjacency = mesh.face_adjacency[selected[mesh.face_adjacency].all(axis=1)]
        for group in trimesh.graph.connected_components(adjacency, nodes=faces, min_len=1):
            group = np.asarray(group)
            area = float(mesh.area_faces[group].sum())
            if area < min_area:
                continue
            centers = mesh.triangles_center[group]
            centroid = (centers * mesh.area_faces[group, None]).sum(axis=0) / area
            corners = mesh.vertices[mesh.faces[group]].reshape(-1, 3)
            regions.append({
                "center": _vec(centers[np.argmin(np.linalg.norm(centers - centroid, axis=1))]),
                "bbox_min": _vec(corners.min(axis=0)),
                "bbox_max": _vec(corners.max(axis=0)),
                "area_mm2": _r3(area),
                "max_angle_deg": _r3(past_vertical[group].max()),
            })
    if not regions:
        return _result("overhang", "pass", f"No overhangs past {angle}°", measured=0.0, limit=_r3(angle))
    regions.sort(key=lambda r: -r["area_mm2"])
    total = sum(r["area_mm2"] for r in regions)
    return _result(
        "overhang", "warn",
        f"{total:.1f} mm² of overhang past {angle}° in {len(regions)} region(s); chamfer, reorient or use supports",
        measured=_r3(total), limit=_r3(angle), regions=regions[:MAX_REGIONS],
    )


def worst_status(statuses):
    worst = max(statuses, key=STATUS_ORDER.__getitem__, default="pass")
    return "pass" if worst == "skipped" else worst


def check_part(mesh, thresholds, expected_size=None):
    """Run every check on one part's mesh. Returns {"status", "mesh", "results"}."""
    t = thresholds
    sealed = check_sealed(mesh)
    if len(mesh.faces) == 0:
        return {
            "status": "fail",
            "mesh": {"triangles": 0, "watertight": False},
            "results": [sealed] + [_result(name, "skipped", "mesh is empty") for name in CHECK_NAMES[1:]],
        }
    results = [sealed, check_size(mesh, expected_size, t["size_tol"]), check_bed_fit(mesh, t)]
    if sealed["status"] == "fail":
        results += [_result(name, "skipped", "mesh not sealed: fix that first") for name in ("wall", "thin", "overhang")]
    else:
        points, thickness = sample_thickness(mesh, t["samples"])
        radius = 2 * t["min_wall"]
        results.append(check_thickness("wall", points, thickness, t["min_wall"], radius))
        results.append(check_thickness("thin", points, thickness, t["min_feature"], radius))
        results.append(check_overhang(mesh, t))
    return {
        "status": worst_status(r["status"] for r in results),
        "mesh": {"triangles": int(len(mesh.faces)), "watertight": bool(mesh.is_watertight)},
        "results": results,
    }
