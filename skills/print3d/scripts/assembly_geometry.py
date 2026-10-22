"""print3d assembly geometry: exact measurements on placed build123d shapes, all in one common frame (mm).

When a feature can't be found the functions say so (`found=False`, `None`); they never return a plausible number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from build123d import Compound, Face, Plane, Solid, Vector, Wire
from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
from OCP.gp import gp_Lin
from scipy.spatial import QhullError, Voronoi, cKDTree

OVERLAP_EPS = 0.01  # mm^3, smaller overlaps count as touching (boolean slivers on shared faces)
AXIS_LIMIT = 0.1  # mm, coaxiality
CONTACT_LIMIT = 0.1  # mm, seat and head gaps
TARGET_TOL = 0.1  # mm, default target tolerance

END_INSET = 0.2  # mm, hole samples stay this far inside the span along the axis
MIN_SAMPLES = 3
WIRE_SPACING = 0.05  # mm, hole wires are discretised at this spacing
INSCRIBED_TOL = 0.01  # mm, inscribed circles this close to the largest tie, and the one nearest the axis wins
CONTACT_SLACK = 0.02  # mm, wall points this far beyond an inscribed circle still touch it
MAX_CONTACT_GAP = 185.0  # degrees, a circle whose wall contacts leave a wider gap is not enclosed by a hole
CLIP_FACTOR = 2.0  # the shape is clipped to a cylinder of this many search radii about the axis
REGION_FACTOR = 1.9  # the free region is looked for within this many search radii (inside the clip)
EDGE_MATCH = 1e-3  # mm, how close an edge must lie to the region circle to count as part of it
RAY_TOL = 1e-6  # mm


@dataclass
class HoleProbe:
    found: bool
    offset: float  # largest distance from the axis to the hole centre over the sampled depths
    diameter: float  # smallest inscribed diameter
    free_radius: float  # smallest distance from the axis to the wall
    depth_span: tuple[float, float]  # along the axis from the origin, where the shape is within search_r
    samples: int


def _pieces(result):
    """`intersect` returns None, one shape or a ShapeList."""
    if result is None:
        return []
    if isinstance(result, (list, tuple)):
        return [shape for shape in result if shape is not None]
    return [result]


def _boxes_meet(a, b):
    box_a, box_b = a.bounding_box(), b.bounding_box()
    return all(
        lo_a <= hi_b and lo_b <= hi_a
        for lo_a, hi_a, lo_b, hi_b in zip(box_a.min, box_a.max, box_b.min, box_b.max)
    )


def overlap(a, b):
    """The intersection volume of a and b and the centre of its bounding box, or (0.0, None) below OVERLAP_EPS."""
    if not _boxes_meet(a, b):
        return 0.0, None
    solids = [piece for piece in _pieces(a.intersect(b)) if piece.volume > 0]
    volume = sum(piece.volume for piece in solids)
    if volume < OVERLAP_EPS:
        return 0.0, None
    centre = Compound(solids).bounding_box().center()
    return volume, tuple(centre)


def min_distance(a, b):
    """The smallest distance between a and b, or 0.0 when they overlap."""
    if overlap(a, b)[0] > 0:
        return 0.0
    return a.distance_to(b)


def section_faces(shape, point, normal):
    """The faces where the plane through point with this normal cuts shape."""
    cut = _pieces(shape.intersect(Plane(origin=point, z_dir=normal)))
    return [face for piece in cut for face in piece.faces()]


def probe(shape, origin, direction):
    """The distance along the ray from origin to the first surface of shape, or None when the ray misses."""
    start, heading = Vector(origin), Vector(direction).normalized()
    hits = BRepIntCurveSurface_Inter()
    hits.Init(shape.wrapped, gp_Lin(start.to_pnt(), heading.to_dir()), RAY_TOL)
    ahead = []
    while hits.More():
        if hits.W() >= -RAY_TOL:
            ahead.append(max(hits.W(), 0.0))
        hits.Next()
    return min(ahead) if ahead else None


def _clip(shape, radius, z_lo, z_hi):
    """The part of shape (in the axis frame) within radius of the Z axis, or None."""
    cylinder = Solid.make_cylinder(radius, z_hi - z_lo, Plane.XY.offset(z_lo))
    solids = [piece for piece in _pieces(shape.intersect(cylinder)) if piece.volume > 0]
    return Compound(solids) if solids else None


def _sample_depths(lo, hi, step):
    lo, hi = lo + END_INSET, hi - END_INSET
    if hi < lo:
        lo = hi = (lo + hi) / 2
    count = max(MIN_SAMPLES, math.ceil((hi - lo) / step) + 1)
    return np.linspace(lo, hi, count)


def _discretise(edge):
    count = max(2, math.ceil(edge.length / WIRE_SPACING))
    return np.array([(p.X, p.Y) for p in edge.positions(np.linspace(0.0, 1.0, count + 1))])


def _inside(points, segments):
    """Even-odd test of points (m x 2) against closed boundaries given as segments (s x 2 x 2)."""
    a, b = segments[:, 0], segments[:, 1]
    px, py = points[:, 0:1], points[:, 1:2]
    straddles = (a[:, 1] > py) != (b[:, 1] > py)
    with np.errstate(divide="ignore", invalid="ignore"):
        x_cross = a[:, 0] + (py - a[:, 1]) * (b[:, 0] - a[:, 0]) / (b[:, 1] - a[:, 1])
    return np.count_nonzero(straddles & (px < x_cross), axis=1) % 2 == 1


def _enclosed(centre, radius, walls, tree):
    """True when the wall points touching this circle surround it (no gap wider than MAX_CONTACT_GAP)."""
    touching = walls[tree.query_ball_point(centre, radius + CONTACT_SLACK)]
    if len(touching) < 2:
        return False
    angles = np.sort(np.degrees(np.arctan2(touching[:, 1] - centre[1], touching[:, 0] - centre[0])))
    gaps = np.diff(np.append(angles, angles[0] + 360.0))
    return gaps.max() <= MAX_CONTACT_GAP


def _largest_inscribed(walls, segments):
    """The (centre, radius) of the largest wall-enclosed circle inside the region, nearest the origin on ties."""
    if len(walls) < 4:
        return None
    try:
        vertices = Voronoi(walls).vertices
    except QhullError:
        return None
    vertices = vertices[_inside(vertices, segments)] if len(vertices) else vertices
    if not len(vertices):
        return None
    tree = cKDTree(walls)
    radii = tree.query(vertices)[0]
    best = None
    for index in np.argsort(-radii):
        if best is not None and radii[index] < best[1] - INSCRIBED_TOL:
            break
        if not _enclosed(vertices[index], radii[index], walls, tree):
            continue
        if best is None:
            best = [vertices[index], radii[index], radii[index]]
        elif np.hypot(*vertices[index]) < np.hypot(*best[0]):
            best[0], best[2] = vertices[index], radii[index]
    return None if best is None else (best[0], best[2])


def _hole_at_depth(clipped, z, search_r):
    """One section of the axis-frame shape at z: "skip", "blocked", "none", or (offset, diameter, free_radius)."""
    faces = _pieces(clipped.intersect(Plane.XY.offset(z)))
    faces = [face for piece in faces for face in piece.faces()]
    if not faces:
        return "skip"
    axis_point = Vector(0, 0, z)
    region_r = REGION_FACTOR * search_r
    region = Face(Wire.make_circle(region_r, Plane.XY.offset(z))).cut(*faces)
    free = [face for face in region.faces() if face.is_inside(axis_point)]
    if not free:
        return "blocked"  # the axis point is inside material
    walls, segments = [], []
    for edge in free[0].edges():
        points = _discretise(edge)
        segments.append(np.stack([points[:-1], points[1:]], axis=1))
        if np.abs(np.hypot(points[:, 0], points[:, 1]) - region_r).max() > EDGE_MATCH:
            walls.append(points)  # material, not the edge of the search region
    if not walls:
        return "skip"
    walls = np.unique(np.concatenate(walls).round(9), axis=0)
    free_radius = cKDTree(walls).query((0.0, 0.0))[0]
    if free_radius > search_r:
        return "skip"
    circle = _largest_inscribed(walls, np.concatenate(segments))
    if circle is None:
        return "none"
    centre, radius = circle
    return float(np.hypot(*centre)), 2.0 * float(radius), float(free_radius)


def hole_on_axis(shape, origin, direction, *, search_r: float, step: float = 1.0) -> HoleProbe:
    """Find the hole (or edge notch) in shape that the axis through origin along direction passes through.

    The axis must run through free space at every sampled depth, and the hole must be narrower than search_r.
    """
    local = Plane(origin=origin, z_dir=direction).to_local_coords(shape)
    box = local.bounding_box()
    z_lo, z_hi = box.min.Z - 1.0, box.max.Z + 1.0
    clipped = _clip(local, CLIP_FACTOR * search_r, z_lo, z_hi)
    near = _clip(clipped, search_r, z_lo, z_hi) if clipped is not None else None
    if near is None:
        return HoleProbe(False, math.nan, math.nan, math.nan, (math.nan, math.nan), 0)
    span_box = near.bounding_box()
    span = (span_box.min.Z, span_box.max.Z)
    measured = []
    for z in _sample_depths(*span, step):
        result = _hole_at_depth(clipped, z, search_r)
        if result in ("blocked", "none"):
            return HoleProbe(False, math.nan, math.nan, math.nan, span, len(measured))
        if result != "skip":
            measured.append(result)
    if not measured:
        return HoleProbe(False, math.nan, math.nan, math.nan, span, 0)
    offsets, diameters, free_radii = zip(*measured)
    return HoleProbe(True, max(offsets), min(diameters), min(free_radii), span, len(measured))
