"""print3d assembly geometry: exact measurements on placed build123d shapes, all in one common frame (mm).

When a feature can't be found the functions say so (`found=False`, `None`); they never return a plausible number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from build123d import Compound, Face, Location, Plane, Solid, Vector, Wire
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
SHADOW_STEP = 0.25  # mm, an opening's shadow is built from sections this far apart (and END_INSET inside each slab)
SHADOW_BATCH = 8  # sections added to a shadow between tests of whether it already encloses the line


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
    depths: tuple = ()  # (lo, hi, offset, diameter, free_radius): each measured sample and the depths it stands for


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
    volume, centre, _ = overlap_extent(a, b)
    return volume, centre


def overlap_extent(a, b):
    """As overlap(), plus the size (x, y, z) of the intersection's bounding box (None below OVERLAP_EPS)."""
    if not _boxes_meet(a, b):
        return 0.0, None, None
    solids = [piece for piece in _pieces(a.intersect(b)) if piece.volume > 0]
    volume = sum(piece.volume for piece in solids)
    if volume < OVERLAP_EPS:
        return 0.0, None, None
    box = Compound(solids).bounding_box()
    return volume, tuple(box.center()), tuple(box.size)


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


def _slabs(clipped, lo, hi, extra=()):
    """Depth slabs between the Z levels of the clipped shape's vertices (and `extra` levels): within one the
    section's topology holds."""
    levels = sorted({lo, hi, *(z for z in (*(v.Z for v in clipped.vertices()), *extra) if lo < z < hi)})
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


def _section(clipped, z):
    return [face for piece in _pieces(clipped.intersect(Plane.XY.offset(z))) for face in piece.faces()]


def _search_region(search_r, z):
    return Face(Wire.make_circle(REGION_FACTOR * search_r, Plane.XY.offset(z)))


def _hole_at_depth(local, clipped, z, search_r):
    """One section of the axis-frame shape at z: None (no material near), a failure reason, or the measurements."""
    faces = _section(clipped, z)
    if not faces:
        return None
    region = _search_region(search_r, z).cut(*faces)
    return _hole_in_region(region, z, search_r, lambda: _in_slot_like_wire(local, Vector(0, 0, z), z))


def _hole_in_region(region, z, search_r, slot_like):
    """The hole round the axis in the free region (the search circle minus the material) in the plane at z: None
    (no material near), a failure reason, or (offset, diameter, free_radius). slot_like() says whether an open
    region still lies in a convex, empty closed hole further out."""
    axis_point = Vector(0, 0, z)
    region_r = REGION_FACTOR * search_r
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
        closed = slot_like()
    circle = _largest_inscribed(walls, np.concatenate(segments), closed)
    if circle is None:
        return "not a hole: the material doesn't enclose the axis"
    centre, radius = circle
    return float(np.hypot(*centre)), 2.0 * float(radius), float(free_radius)


def _not_found(span, samples, reason, excluded=()):
    return HoleProbe(False, math.nan, math.nan, math.nan, span, samples, reason, tuple(excluded))


def _runs(owned, keep=()):
    """Merge consecutive (lo, hi, result) depth ranges into runs [lo, hi, samples or reason, qualifies].

    A qualifying (tuple) run keeps its samples [(lo, hi, result)]. Excluded (str) results merge into one run under
    the first reason, except that a reason in `keep` (a skipped range) stays a run of its own.
    """
    runs = []
    for lo, hi, result in owned:
        qualifies = isinstance(result, tuple)
        same = runs and runs[-1][3] == qualifies and (
            qualifies or result == runs[-1][2] or (result not in keep and runs[-1][2] not in keep))
        if same:
            runs[-1][1] = hi
            if qualifies:
                runs[-1][2].append((lo, hi, result))
        else:
            runs.append([lo, hi, [(lo, hi, result)] if qualifies else result, qualifies])
    return runs


def _length(ranges):
    """The total length covered by (lo, hi, ...) ranges, overlaps counted once."""
    total, reach = 0.0, -math.inf
    for lo, hi, *_ in sorted(ranges, key=lambda r: r[0]):
        lo = max(lo, reach)
        if hi > lo:
            total += hi - lo
            reach = hi
    return total


def hole_on_axis(shape, origin, direction, *, search_r: float, step: float = 1.0, span=None, skip=(),
                 wide=None) -> HoleProbe:
    """Find the hole (or edge notch) in shape that the axis through origin along direction passes through.

    The axis line must not cross any material. The depths along it are split into slabs at the shape's vertex
    levels and sampled; a depth qualifies when the section there is a hole narrower than search_r around the axis
    (a closed, convex and empty inner wire, or material covering at least 200 degrees round a notch). Depths that
    don't qualify, such as beside a wall or in open space, are excluded rather than measured, and listed in
    `excluded`. The hole is found when the qualifying depths include one contiguous range at least
    min(MIN_HOLE_DEPTH, the extent of material within search_r, less any skipped and wide depths) long; offset,
    diameter, free_radius and depth_span come from all the qualifying depths only. Where the free region around the axis runs past 1.9 x search_r, only
    a convex, empty closed hole skips the notch cover test, so a long arc slot or a slot with a nick in its edge
    reports "not a hole" (it fails safe).

    span=(lo, hi) limits everything to that range along the axis from origin (e.g. a screw from its tip to its
    seating plane): only material there can block the axis or make a hole, and material beyond it (the floor of a
    blind hole past the tip, a part further down the axis) is ignored.

    skip=((lo, hi, reason), ...) are depth ranges excluded with that reason and not measured (e.g. where a nut sits
    in its pocket); material there still blocks the axis. They don't apply when they would cover all the material
    within search_r: then that material is all the hole there is.

    wide=(diameter, reason): qualifying depths whose hole is at least this wide (a nut's tunnel running on past the
    nut) are excluded with that reason and not measured, and their length leaves the need like a skip's. This
    doesn't apply when no qualifying depth would be left: then the wide hole is all the hole there is.
    """
    local = Plane(origin=origin, z_dir=direction).to_local_coords(shape)
    box = local.bounding_box()
    z_lo, z_hi = box.min.Z - 1.0, box.max.Z + 1.0
    within = ""
    if span is not None:
        z_lo, z_hi = max(z_lo, span[0]), min(z_hi, span[1])
        within = f" between {span[0]:.2f} and {span[1]:.2f} mm along it"
    clipped = _clip(local, CLIP_FACTOR * search_r, z_lo, z_hi) if z_hi - z_lo > RAY_TOL else None
    near = _clip(clipped, search_r, z_lo, z_hi) if clipped is not None else None
    if near is None:
        return _not_found((math.nan, math.nan), 0, f"no material within {search_r} mm of the axis{within}")
    span_box = near.bounding_box()
    span = (span_box.min.Z, span_box.max.Z)
    # crossings at the range's ends are the clip's own cap faces (or a hole floor exactly at the tip): not blocking
    crossings = [w for w in _line_hits(clipped, (0, 0, 0), (0, 0, 1)) if z_lo + RAY_TOL < w < z_hi - RAY_TOL]
    if crossings:  # catches layers thinner than the sample step and the end insets
        return _not_found(span, 0, f"blocked: material on the axis at {crossings[0]:.2f} mm along it")
    skip = [(max(lo, span[0]), min(hi, span[1]), why) for lo, hi, why in skip
            if min(hi, span[1]) - max(lo, span[0]) > RAY_TOL]
    if skip and _length(skip) >= span[1] - span[0] - RAY_TOL:
        skip = []
    open_reason = f"open: no wall within {search_r} mm of the axis"
    owned = []  # (lo, hi, result): each sample owns the depths up to half way to its neighbours in the slab
    for lo, hi in _slabs(clipped, *span, extra=[z for a, b, _ in skip for z in (a, b)]):
        depths = _slab_depths(lo, hi, step)
        bounds = [lo, *((a + b) / 2 for a, b in zip(depths[:-1], depths[1:])), hi]
        for index, z in enumerate(depths):
            result = _hole_at_depth(local, clipped, z, search_r)
            if isinstance(result, str) and result.startswith("blocked"):
                return _not_found(span, 0, f"{result} at depth {z:.2f} mm along the axis")
            skipped = next((why for a, b, why in skip if a < z < b), None)
            owned.append((bounds[index], bounds[index + 1], skipped or (open_reason if result is None else result)))
    keep, tunnel = {why for _, _, why in skip}, []
    if wide is not None:
        narrow = [entry for entry in owned if isinstance(entry[2], tuple) and entry[2][1] < wide[0]]
        tunnel = [(lo, hi, wide[1]) for lo, hi, result in owned if isinstance(result, tuple) and result[1] >= wide[0]]
        if narrow and tunnel:
            owned = [(lo, hi, wide[1]) if isinstance(result, tuple) and result[1] >= wide[0] else (lo, hi, result)
                     for lo, hi, result in owned]
            keep.add(wide[1])
        else:
            tunnel = []
    runs = _runs(owned, keep=keep)
    excluded = tuple((lo, hi, reason) for lo, hi, reason, qualifies in runs if not qualifies)
    holes = [(lo, hi, samples) for lo, hi, samples, qualifies in runs if qualifies]
    # what a skip (or a wide tunnel) leaves is all there is to measure
    need = min(MIN_HOLE_DEPTH, span[1] - span[0] - _length(skip) - _length(tunnel))
    longest = max((hi - lo for lo, hi, _ in holes), default=0.0)
    if longest < need - RAY_TOL:
        where = "; ".join(f"{reason} at depths {lo:.2f} to {hi:.2f} mm" for lo, hi, reason in excluded)
        not_hole = [reason for _, _, reason in excluded if reason.startswith("not a hole")]
        if holes:
            reason = (f"not a hole: only {longest:.2f} mm along the axis is a hole, it needs {need:.2f} mm "
                      f"(elsewhere {where})")
        elif not_hole:
            reason = where if where.startswith("not a hole") else f"not a hole: {where}"
        else:
            reason = f"no material within {search_r} mm of the axis at the sampled depths"
        return _not_found(span, sum(len(samples) for _, _, samples in holes), reason, excluded)
    depths = tuple((lo, hi, *result) for _, _, samples in holes for lo, hi, result in samples)
    offsets, diameters, free_radii = zip(*(d[2:] for d in depths))
    depth_span = (holes[0][0], holes[-1][1])
    return HoleProbe(True, max(offsets), min(diameters), min(free_radii), depth_span, len(depths), "", excluded,
                     depths)


@dataclass
class PocketSection:
    found: bool
    offset: float  # distance in the section plane from the point to the pocket's centre
    min_width: float  # smallest width of the pocket outline (its convex hull's narrowest caliper)
    closed: bool  # the pocket is an inner wire of the section; otherwise it opens out past `radius`
    reason: str = ""


def _min_width(points):
    hull = points[ConvexHull(points).vertices]
    best = math.inf
    for a, b in zip(hull, np.roll(hull, -1, axis=0)):
        edge = b - a
        normal = np.array([-edge[1], edge[0]]) / np.hypot(*edge)
        best = min(best, float(np.abs((hull - a) @ normal).max()))
    return best


def pocket_section(shape, point, normal, radius) -> PocketSection:
    """The opening of shape's section through point (plane normal `normal`) that point lies in, e.g. a nut pocket.

    A closed pocket is the inner wire around point: its centre is the wire's centroid. An open pocket (a side-loaded
    nut trap) is the free region around point within 2 x radius: its centre is its largest inscribed circle's
    (across the slot; along it, the circle nearest point), and the clipped outline counts towards its width.
    """
    local = Plane(origin=point, z_dir=normal).to_local_coords(shape)
    faces = [face for piece in _pieces(local.intersect(Plane.XY)) for face in piece.faces()]
    if not faces:
        return PocketSection(False, math.nan, math.nan, False, "no material in the section")
    origin = Vector(0, 0, 0)
    for wire in (wire for face in faces for wire in face.inner_wires()):
        pocket = Face(wire)
        if pocket.is_inside(origin):
            outline = np.concatenate([_discretise(edge) for edge in wire.edges()])
            centre = pocket.center()
            return PocketSection(True, float(math.hypot(centre.X, centre.Y)), _min_width(outline), True)
    region_r = 2.0 * radius
    region = Face(Wire.make_circle(region_r)).cut(*faces)
    free = [face for face in region.faces() if face.is_inside(origin)]
    if not free:
        return PocketSection(False, math.nan, math.nan, False, "the point is inside material")
    walls, segments, outline = [], [], []
    for edge in free[0].edges():
        points = _discretise(edge)
        outline.append(points)
        segments.append(np.stack([points[:-1], points[1:]], axis=1))
        if np.abs(np.hypot(points[:, 0], points[:, 1]) - region_r).max() > EDGE_MATCH:
            walls.append(points)
    if not walls:
        return PocketSection(False, math.nan, math.nan, False, f"no pocket wall within {region_r:g} mm")
    walls = np.unique(np.concatenate(walls).round(9), axis=0)
    circle = _largest_inscribed(walls, np.concatenate(segments), True)
    if circle is None:
        return PocketSection(False, math.nan, math.nan, False, "the walls don't enclose the point")
    return PocketSection(True, float(np.hypot(*circle[0])), _min_width(np.concatenate(outline)), False)


# --- passage openings ---------------------------------------------------------------------------------------------


@dataclass
class OpeningProbe:
    found: bool
    span: tuple  # (lo, hi) along the line from origin: the part's material that makes the opening (found)
    offset: float  # distance from the line to the centre of the opening's largest inscribed circle, in its shadow
    diameter: float  # that circle's diameter: the clear aperture along the line
    reason: str = ""


def _encloses(result):
    """True for a hole whose largest inscribed circle contains the axis."""
    return isinstance(result, tuple) and result[0] <= result[1] / 2


def _ranges(near):
    """The depth ranges (lo, hi) of near's solids, merged where they touch or overlap."""
    ranges = []
    for lo, hi in sorted((s.bounding_box().min.Z, s.bounding_box().max.Z) for s in near.solids()):
        if ranges and lo <= ranges[-1][1] + BREAK_MERGE:
            ranges[-1][1] = max(ranges[-1][1], hi)
        else:
            ranges.append([lo, hi])
    return [tuple(r) for r in ranges]


def _shadow(clipped, lo, hi, search_r):
    """The hole round the axis in the shadow along it of the material between depths lo and hi (the union of its
    sections, SHADOW_STEP apart): None, a failure reason, or (offset, diameter, free_radius). Stops as soon as the
    shadow encloses the axis. The union of sampled sections is part of the true shadow, so a gap between samples can
    only make it fail."""
    depths = [z for a, b in _slabs(clipped, lo, hi) for z in _slab_depths(a, b, SHADOW_STEP)]
    region, result = _search_region(search_r, 0.0), None
    for start in range(0, len(depths), SHADOW_BATCH):
        faces = [face.moved(Location((0, 0, -z))) for z in depths[start:start + SHADOW_BATCH]
                 for face in _section(clipped, z)]
        if faces:
            region = region.cut(*faces)
            result = _hole_in_region(region, 0.0, search_r, lambda: False)
            if _encloses(result) or (isinstance(result, str) and result.startswith("blocked")):
                return result
    return result


def _ends_in_opening(local, clipped, end, inward, part_range, search_r):
    """True when the stand-in, ending at depth `end` (its body on the `inward` side, +1 or -1), ends inside an
    opening of the part: the section just inside its end (END_INSET, or half the part's material on that side when
    the stand-in enters it less deeply) encloses the axis, or the shadow of the material within search_r of the end
    does while neither side's alone does (an opening that runs across the end)."""
    entered = (part_range[1] - end) if inward > 0 else (end - part_range[0])
    inside = end + inward * min(END_INSET, entered / 2)
    if _encloses(_hole_at_depth(local, clipped, inside, search_r)):
        return True
    return _across(clipped, end, part_range, search_r)


def _across(clipped, z, part_range, search_r):
    """True when an opening runs across depth z: the shadow of the material within search_r of z encloses the axis,
    while neither side's alone does."""
    reach = (max(part_range[0], z - search_r), min(part_range[1], z + search_r))
    if not _encloses(_shadow(clipped, *reach, search_r)):
        return False
    below, above = (reach[0], z), (z, reach[1])
    return not any(hi - lo > RAY_TOL and _encloses(_shadow(clipped, lo, hi, search_r)) for lo, hi in (below, above))


def _entry(local, clipped, end, inward, part_range, search_r, limit=MIN_HOLE_DEPTH):
    """How far (up to limit) back from its end, on its `inward` side, the stand-in stays inside the opening.

    The depths are walked per slab (so a hole's own face is found exactly) while their sections enclose the axis.
    At the first that doesn't, an opening running across it (_across, a slanted slot) carries the walk on only when
    one also runs across the limit; otherwise the walk stops there. At the first sample that is known (the caller
    found the end inside an opening, and not by its section), so this costs one or two _across tests."""
    lo, hi = sorted((end, end + inward * limit))
    slabs = _slabs(clipped, lo, hi) if hi - lo > RAY_TOL else []
    reached = 0.0
    for s0, s1 in (slabs if inward > 0 else slabs[::-1]):
        depths = _slab_depths(s0, s1, SHADOW_STEP)
        for z in (depths if inward > 0 else depths[::-1]):
            if _encloses(_hole_at_depth(local, clipped, z, search_r)):
                reached = abs(z - end)
                continue
            # the first sample: the end is inside by its shadow (_ends_in_opening), so an opening runs across it
            if reached > 0 and not _across(clipped, z, part_range, search_r):
                return reached
            return limit if _across(clipped, end + inward * limit, part_range, search_r) else abs(z - end)
        reached = abs((s1 if inward > 0 else s0) - end)
    return limit


def _ends_in_two_runs(local, clipped, extent, part_range, search_r, hits, where, reasons):
    """ends_inside with the body's material running on past both ends of the stand-in, each end inside an opening:
    an OpeningProbe when the ends sit in two different opening runs (a section between them doesn't enclose the
    axis) and each is entered by min(MIN_HOLE_DEPTH, half its run), else None (adding why not to `reasons`). Each
    end's shadow, from search_r on its body side to search_r past it (or the first material on the line past it),
    must enclose the axis; the result has the largest offset and the smallest diameter of the two."""
    (e0, e1), (a, b) = extent, part_range
    if all(_encloses(_hole_at_depth(local, clipped, (s0 + s1) / 2, search_r)) for s0, s1 in _slabs(clipped, e0, e1)):
        reasons.append(f"it ends inside an opening at {e0:.2f} mm along its line and doesn't enter it from a face of "
                       f"the part ({where})")
        return None
    for end, inward in ((e0, 1), (e1, -1)):
        entered = _entry(local, clipped, end, inward, part_range, search_r)
        outer = a if inward > 0 else b
        need = min(MIN_HOLE_DEPTH, abs(end + inward * entered - outer) / 2)
        if entered < need - RAY_TOL:
            reasons.append(f"it enters the opening only {entered:.2f} mm, it needs {need:.2f} mm "
                           f"(it ends at {end:.2f} mm along its line; {where})")
            return None
    crossings = [w for w in hits if e0 + RAY_TOL < w < e1 - RAY_TOL]
    if crossings:
        reasons.append(f"blocked: material on its line at {crossings[0]:.2f} mm along it")
        return None
    results = []
    for seen in ((max([a, e0 - search_r, *(w for w in hits if w < e0 + RAY_TOL)]), min(e1, e0 + search_r)),
                 (max(e0, e1 - search_r), min([b, e1 + search_r, *(w for w in hits if w > e1 - RAY_TOL)]))):
        result = _shadow(clipped, *seen, search_r)
        if not _encloses(result):
            why = "no wall near the line" if result is None else (
                result if isinstance(result, str) else "the opening's widest circle doesn't contain the line")
            reasons.append(f"{why}, seen along the line from {seen[0]:.2f} to {seen[1]:.2f} mm")
            return None
        results.append(result)
    return OpeningProbe(True, (e0, e1), max(r[0] for r in results), min(r[1] for r in results))


def passage_opening(shape, origin, direction, *, search_r: float, extent, ends_inside: bool = False) -> OpeningProbe:
    """Find an opening of shape that the stand-in on the line through origin along direction, over depths
    extent = (lo, hi) along it, passes through.

    Each body (solid) of the part is looked at on its own: separate bodies never make one opening together, even
    where they touch. A body's material within search_r of the line falls into depth ranges (its pieces there,
    merged where their depths overlap or touch; one body's pieces may join further out). A range is an opening the
    stand-in passes when:
    - it lies within the stand-in's extent, or the body's material runs on past an end of the stand-in but the
      stand-in doesn't end inside an opening there (see _ends_in_opening);
    - the line crosses no material of the part in it;
    - the shadow along the line of the body's material within the range and the extent (the union of its
      sections) encloses the line:
      the section rules of hole_on_axis (a closed region, a convex empty slot, or a notch with 200 degrees of
      cover) applied to the shadow, and the shadow's largest inscribed circle contains the line.
    Measuring the shadow, not single sections, is what finds a slot in a wall oblique to the line: no single
    section there closes round the line. Material beyond the stand-in's extent (a box's far wall) doesn't count.

    ends_inside=True: a stand-in that ends inside an opening (as _ends_in_opening finds it) may end there, provided
    the range's other end lies within its extent (it entered the opening from that face of the body) and it runs
    at least min(MIN_HOLE_DEPTH, half the range) into the range from that face. The line must cross no material
    from that face to the stand-in's end, and the shadow from that face to search_r past the end, or to the first
    material on the line past it (a blind hole's floor), must enclose the line. With the body's material running on
    past both ends and both inside openings, the ends must sit in two different opening runs, each entered by
    min(MIN_HOLE_DEPTH, half its run) (see _ends_in_two_runs); a stand-in wholly inside one opening run fails. So
    does one that ends before an opening (that is no opening across its end).
    """
    local = Plane(origin=origin, z_dir=direction).to_local_coords(shape)
    box = local.bounding_box()
    z_lo, z_hi = box.min.Z - 1.0, box.max.Z + 1.0
    whole = _clip(local, CLIP_FACTOR * search_r, z_lo, z_hi)
    missing = OpeningProbe(False, (math.nan, math.nan), math.nan, math.nan,
                           f"no material within {search_r:g} mm of its run line")
    if whole is None or _clip(whole, search_r, z_lo, z_hi) is None:
        return missing
    bodies = []
    for body in local.solids():
        clipped = _clip(body, CLIP_FACTOR * search_r, z_lo, z_hi) if _boxes_meet(body, whole) else None
        near = _clip(clipped, search_r, z_lo, z_hi) if clipped is not None else None
        if near is not None:
            bodies.append((body, clipped, near))
    e0, e1 = extent
    reasons = []
    for body, clipped, near in bodies:
        probe = _body_opening(body, clipped, near, whole, (e0, e1), search_r, reasons, ends_inside)
        if probe is not None:
            return probe
    return OpeningProbe(False, (math.nan, math.nan), math.nan, math.nan, "; ".join(reasons) or missing.reason)


def _body_opening(local, clipped, near, whole, extent, search_r, reasons, ends_inside=False):
    """passage_opening for one body (local, clipped to the search reach, and its near pieces): the OpeningProbe of
    its first range that is an opening, or None (adding why not to `reasons`). `whole` is all of the part, clipped:
    any of its material on the line blocks."""
    e0, e1 = extent
    for a, b in _ranges(near):
        lo, hi = max(a, e0), min(b, e1)
        where = f"its material near the line from {a:.2f} to {b:.2f} mm along it"
        if hi - lo <= RAY_TOL:
            reasons.append(f"{where} lies beyond the stand-in ({e0:.2f} to {e1:.2f})")
            continue
        ends = [(end, inward) for end, inward, runs_on in ((e0, 1, a < e0 - RAY_TOL), (e1, -1, b > e1 + RAY_TOL))
                if runs_on]
        inside = [(end, inward) for end, inward in ends
                  if _ends_in_opening(local, clipped, end, inward, (a, b), search_r)]
        if ends_inside and len(inside) == 2:  # both ends inside: they must sit in two different opening runs
            probe = _ends_in_two_runs(local, clipped, (e0, e1), (a, b), search_r,
                                      _line_hits(whole, (0, 0, 0), (0, 0, 1)), where, reasons)
            if probe is not None:
                return probe
            continue
        if inside and not (ends_inside and len(ends) == 1):  # with ends_inside: entered from the range's other end
            entry = " and doesn't enter it from a face of the part" if ends_inside else ""
            reasons.append(f"it ends inside an opening at {inside[0][0]:.2f} mm along its line{entry} ({where})")
            continue
        if inside:  # ends_inside: it must enter the body by min(MIN_HOLE_DEPTH, half its material) from that face
            end, inward = inside[0]
            entered, need = (end - a) if inward < 0 else (b - end), min(MIN_HOLE_DEPTH, (b - a) / 2)
            if entered < need - RAY_TOL:
                reasons.append(f"it enters the opening only {entered:.2f} mm, it needs {need:.2f} mm "
                               f"(it ends at {end:.2f} mm along its line; {where})")
                continue
        hits = _line_hits(whole, (0, 0, 0), (0, 0, 1))
        crossings = [w for w in hits if lo + RAY_TOL < w < hi - RAY_TOL]
        if crossings:
            reasons.append(f"blocked: material on its line at {crossings[0]:.2f} mm along it")
            continue
        seen = (lo, hi)
        if inside:  # the opening runs across the end: see it out to search_r past the end, or to the first material
            # on the line past it (a blind hole's floor)
            if inward < 0:
                seen = (lo, min([b, end + search_r, *(w for w in hits if w > end - RAY_TOL)]))
            else:
                seen = (max([a, end - search_r, *(w for w in hits if w < end + RAY_TOL)]), hi)
        result = _shadow(clipped, *seen, search_r)
        if _encloses(result):
            return OpeningProbe(True, (lo, hi), result[0], result[1])
        why = "no wall near the line" if result is None else (
            result if isinstance(result, str) else "the opening's widest circle doesn't contain the line")
        reasons.append(f"{why}, seen along the line from {seen[0]:.2f} to {seen[1]:.2f} mm")
    return None
