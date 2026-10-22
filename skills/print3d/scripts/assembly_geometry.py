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
from scipy.spatial import ConvexHull, QhullError, Voronoi, cKDTree

OVERLAP_EPS = 0.01  # mm^3, smaller overlaps count as touching (boolean slivers on shared faces)
AXIS_LIMIT = 0.1  # mm, coaxiality
CONTACT_LIMIT = 0.1  # mm, seat and head gaps
TARGET_TOL = 0.1  # mm, default target tolerance

END_INSET = 0.2  # mm, hole samples stay this far inside each depth slab along the axis
MIN_HOLE_DEPTH = 1.0  # mm, a hole must qualify over this long a range (or the whole material extent, if shorter)
BREAK_MERGE = 1e-3  # mm, depth breakpoints closer than this are one
WIRE_SPACING = 0.05  # mm, hole wires are discretised at this spacing
INSCRIBED_TOL = 0.01  # mm, inscribed circles this close to the largest tie, and the one nearest the axis wins
CONTACT_SLACK = 0.02  # mm, wall points this far beyond an inscribed circle still touch it
MAX_CONTACT_GAP = 185.0  # degrees, a circle whose wall contacts leave a wider gap is not enclosed by a hole
NOTCH_REACH = 1.3  # in an open region, material within this many inscribed radii counts as surrounding the circle
MIN_NOTCH_COVER = 200.0  # degrees of surrounding material a notch needs (a parallel open channel has about 160)
COVER_BREAK = 10.0  # degrees, a wider angular gap between material points is uncovered
CONVEX_RATIO = 0.99  # an inner wire is slot-like when its area is at least this share of its convex hull's
ISLAND_AREA = 0.01  # mm^2, material inside an inner wire beyond this means it holds islands, so it isn't slot-like
CLIP_FACTOR = 2.0  # the shape is clipped to a cylinder of this many search radii about the axis
REGION_FACTOR = 1.9  # the free region is looked for within this many search radii (inside the clip)
EDGE_MATCH = 1e-3  # mm, how close an edge must lie to the region circle to count as part of it
RAY_TOL = 1e-6  # mm
RAY_SHIFT = 1.2345e-7  # mm, the even-odd test's rays run just off round numbers


@dataclass
class HoleProbe:
    found: bool
    offset: float  # largest distance from the axis to the hole centre over the sampled depths
    diameter: float  # smallest inscribed diameter
    free_radius: float  # smallest distance from the axis to the wall
    depth_span: tuple[float, float]  # along the axis from the origin: the qualifying depths (found) or the
    # depths where the shape is within search_r (not found)
    samples: int  # qualifying samples measured
    reason: str = ""  # why the hole wasn't found, "" when it was
    excluded: tuple = ()  # (lo, hi, reason) depth ranges near the axis that aren't a hole and weren't measured


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
    ahead = [max(w, 0.0) for w in _line_hits(shape, origin, direction) if w >= -RAY_TOL]
    return min(ahead) if ahead else None


def _line_hits(shape, origin, direction):
    """Signed distances from origin to every surface crossing of the infinite line along direction."""
    start, heading = Vector(origin), Vector(direction).normalized()
    hits = BRepIntCurveSurface_Inter()
    hits.Init(shape.wrapped, gp_Lin(start.to_pnt(), heading.to_dir()), RAY_TOL)
    found = []
    while hits.More():
        found.append(hits.W())
        hits.Next()
    return sorted(found)


def _clip(shape, radius, z_lo, z_hi):
    """The part of shape (in the axis frame) within radius of the Z axis, or None."""
    cylinder = Solid.make_cylinder(radius, z_hi - z_lo, Plane.XY.offset(z_lo))
    solids = [piece for piece in _pieces(shape.intersect(cylinder)) if piece.volume > 0]
    return Compound(solids) if solids else None


def _slabs(clipped, lo, hi):
    """Depth slabs between the Z levels of the clipped shape's vertices: within one the section's topology holds."""
    levels = sorted({lo, hi, *(v.Z for v in clipped.vertices() if lo < v.Z < hi)})
    merged = [levels[0]]
    for z in levels[1:]:
        if z - merged[-1] > BREAK_MERGE:
            merged.append(z)
    merged[-1] = hi
    return list(zip(merged[:-1], merged[1:]))


def _slab_depths(lo, hi, step):
    """Sample depths in one slab, END_INSET inside its ends (just the middle when it is thinner)."""
    if hi - lo <= 2 * END_INSET:
        return [(lo + hi) / 2]
    count = max(2, math.ceil((hi - lo - 2 * END_INSET) / step) + 1)
    return list(np.linspace(lo + END_INSET, hi - END_INSET, count))


def _discretise(edge):
    count = max(2, math.ceil(edge.length / WIRE_SPACING))
    return np.array([(p.X, p.Y) for p in edge.positions(np.linspace(0.0, 1.0, count + 1))])


def _inside(points, segments):
    """Even-odd test of points (m x 2) against closed boundaries given as segments (s x 2 x 2)."""
    a, b = segments[:, 0], segments[:, 1]
    px, py = points[:, 0:1], points[:, 1:2] + RAY_SHIFT  # off the wires' seam vertices, which sit on y = 0
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


def _covered(centre, radius, walls, tree):
    """Degrees around the circle where material lies within NOTCH_REACH radii of its centre."""
    near = walls[tree.query_ball_point(centre, NOTCH_REACH * radius)]
    if len(near) < 2:
        return 0.0
    angles = np.sort(np.degrees(np.arctan2(near[:, 1] - centre[1], near[:, 0] - centre[0])))
    gaps = np.diff(np.append(angles, angles[0] + 360.0))
    return 360.0 - gaps[gaps > COVER_BREAK].sum()


def _hole_rank(centre, radius, walls, tree, closed):
    """None when this circle isn't a hole's; otherwise a tie-break key, lower is better.

    A closed region (inner wire, slot) needs an enclosed circle, and on ties the one nearest the axis wins, because a
    slot's centre slides along it. An open region also needs a notch's cover, and on ties the best-covered circle
    wins: that is the notch's end, which must not slide out along the channel towards the axis.
    """
    if not _enclosed(centre, radius, walls, tree):
        return None
    if closed:
        return float(np.hypot(*centre))
    cover = _covered(centre, radius, walls, tree)
    return None if cover < MIN_NOTCH_COVER else -cover


def _largest_inscribed(walls, segments, closed):
    """The (centre, radius) of the largest hole circle inside the region, tie-broken by `_hole_rank`."""
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
    largest, best = None, None  # best: (rank, centre, radius)
    for index in np.argsort(-radii):
        if largest is not None and radii[index] < largest - INSCRIBED_TOL:
            break
        rank = _hole_rank(vertices[index], radii[index], walls, tree, closed)
        if rank is None:
            continue
        largest = radii[index] if largest is None else largest
        if best is None or rank < best[0]:
            best = (rank, vertices[index], radii[index])
    return None if best is None else (best[1], best[2])


def _in_slot_like_wire(shape, point, z):
    """True when point lies in a convex, empty closed hole (round hole, teardrop, straight slot) of shape's section at z.

    Being inside a larger opening of another shape (a window with prongs reaching or standing in it) doesn't count.
    """
    faces = [face for piece in _pieces(shape.intersect(Plane.XY.offset(z))) for face in piece.faces()]
    for wire in (wire for face in faces for wire in face.inner_wires()):
        hole = Face(wire)
        if hole.is_inside(point):
            outline = np.concatenate([_discretise(edge) for edge in wire.edges()])
            if hole.area < CONVEX_RATIO * ConvexHull(outline).volume:  # a 2D hull's volume is its area
                return False
            empty = sum(piece.area for piece in hole.cut(*faces).faces())
            return hole.area - empty <= ISLAND_AREA  # no other section face stands inside it
    return False


def _hole_at_depth(local, clipped, z, search_r):
    """One section of the axis-frame shape at z: None (no material near), a failure reason, or the measurements."""
    faces = _pieces(clipped.intersect(Plane.XY.offset(z)))
    faces = [face for piece in faces for face in piece.faces()]
    if not faces:
        return None
    axis_point = Vector(0, 0, z)
    region_r = REGION_FACTOR * search_r
    region = Face(Wire.make_circle(region_r, Plane.XY.offset(z))).cut(*faces)
    free = [face for face in region.faces() if face.is_inside(axis_point)]
    if not free:
        return "blocked: the axis is inside material"
    walls, segments, closed = [], [], True
    for edge in free[0].edges():
        points = _discretise(edge)
        segments.append(np.stack([points[:-1], points[1:]], axis=1))
        if np.abs(np.hypot(points[:, 0], points[:, 1]) - region_r).max() > EDGE_MATCH:
            walls.append(points)  # material, not the edge of the search region
        else:
            closed = False  # the free region runs out past the search region
    if not walls:
        return None
    walls = np.unique(np.concatenate(walls).round(9), axis=0)
    free_radius = cKDTree(walls).query((0.0, 0.0))[0]
    if free_radius > search_r:
        return None
    if not closed:  # open within the search region; a long slot is still closed further out
        closed = _in_slot_like_wire(local, axis_point, z)
    circle = _largest_inscribed(walls, np.concatenate(segments), closed)
    if circle is None:
        return "not a hole: the material doesn't enclose the axis"
    centre, radius = circle
    return float(np.hypot(*centre)), 2.0 * float(radius), float(free_radius)


def _not_found(span, samples, reason, excluded=()):
    return HoleProbe(False, math.nan, math.nan, math.nan, span, samples, reason, tuple(excluded))


def _runs(owned):
    """Merge consecutive (lo, hi, result) depth ranges into runs of qualifying (tuple) or excluded (str) results."""
    runs = []
    for lo, hi, result in owned:
        qualifies = isinstance(result, tuple)
        if runs and runs[-1][3] == qualifies:
            runs[-1][1] = hi
            if qualifies:
                runs[-1][2].append(result)
        else:
            runs.append([lo, hi, [result] if qualifies else result, qualifies])
    return runs


def hole_on_axis(shape, origin, direction, *, search_r: float, step: float = 1.0) -> HoleProbe:
    """Find the hole (or edge notch) in shape that the axis through origin along direction passes through.

    The axis line must not cross any material. The depths along it are split into slabs at the shape's vertex
    levels and sampled; a depth qualifies when the section there is a hole narrower than search_r around the axis
    (a closed, convex and empty inner wire, or material covering at least 200 degrees round a notch). Depths that
    don't qualify, such as beside a wall or in open space, are excluded rather than measured, and listed in
    `excluded`. The hole is found when the qualifying depths include one contiguous range at least
    min(MIN_HOLE_DEPTH, the extent of material within search_r) long; offset, diameter, free_radius and depth_span
    come from all the qualifying depths only. Where the free region around the axis runs past 1.9 x search_r, only
    a convex, empty closed hole skips the notch cover test, so a long arc slot or a slot with a nick in its edge
    reports "not a hole" (it fails safe).
    """
    local = Plane(origin=origin, z_dir=direction).to_local_coords(shape)
    box = local.bounding_box()
    z_lo, z_hi = box.min.Z - 1.0, box.max.Z + 1.0
    clipped = _clip(local, CLIP_FACTOR * search_r, z_lo, z_hi)
    near = _clip(clipped, search_r, z_lo, z_hi) if clipped is not None else None
    if near is None:
        return _not_found((math.nan, math.nan), 0, f"no material within {search_r} mm of the axis")
    span_box = near.bounding_box()
    span = (span_box.min.Z, span_box.max.Z)
    crossings = _line_hits(clipped, (0, 0, 0), (0, 0, 1))
    if crossings:  # catches layers thinner than the sample step and the end insets
        return _not_found(span, 0, f"blocked: material on the axis at {crossings[0]:.2f} mm along it")
    open_reason = f"open: no wall within {search_r} mm of the axis"
    owned = []  # (lo, hi, result): each sample owns the depths up to half way to its neighbours in the slab
    for lo, hi in _slabs(clipped, *span):
        depths = _slab_depths(lo, hi, step)
        bounds = [lo, *((a + b) / 2 for a, b in zip(depths[:-1], depths[1:])), hi]
        for index, z in enumerate(depths):
            result = _hole_at_depth(local, clipped, z, search_r)
            if isinstance(result, str) and result.startswith("blocked"):
                return _not_found(span, 0, f"{result} at depth {z:.2f} mm along the axis")
            owned.append((bounds[index], bounds[index + 1], open_reason if result is None else result))
    runs = _runs(owned)
    excluded = tuple((lo, hi, reason) for lo, hi, reason, qualifies in runs if not qualifies)
    holes = [(lo, hi, measured) for lo, hi, measured, qualifies in runs if qualifies]
    need = min(MIN_HOLE_DEPTH, span[1] - span[0])
    longest = max((hi - lo for lo, hi, _ in holes), default=0.0)
    if longest < need - RAY_TOL:
        where = "; ".join(f"{reason} at depths {lo:.2f} to {hi:.2f} mm" for lo, hi, reason in excluded)
        not_hole = [reason for _, _, reason in excluded if reason.startswith("not a hole")]
        if holes:
            reason = (f"not a hole: only {longest:.2f} mm along the axis is a hole, it needs {need:.2f} mm "
                      f"(elsewhere {where})")
        elif not_hole:
            reason = f"not a hole: the material doesn't enclose the axis ({where})"
        else:
            reason = f"no material within {search_r} mm of the axis at the sampled depths"
        return _not_found(span, sum(len(measured) for _, _, measured in holes), reason, excluded)
    measured = [result for _, _, results in holes for result in results]
    offsets, diameters, free_radii = zip(*measured)
    depth_span = (holes[0][0], holes[-1][1])
    return HoleProbe(True, max(offsets), min(diameters), min(free_radii), depth_span, len(measured), "", excluded)
