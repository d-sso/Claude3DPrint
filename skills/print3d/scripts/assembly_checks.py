"""print3d assembly checks: one function per interface type, on PlacedItems in one common frame.

check_interface(iface, items) returns a JSON-ready dict:
  name, type, items (names), status ("pass"/"fail"), measured, expected, source, location [x, y, z], message,
  evidence (filled in by the sheets), planes [(point, normal)] for the evidence sections.
A feature that can't be found fails with a message starting "not found:"; a check never passes on nothing.

Screw-axis positions are reported as t, the distance along the screw's +Z (tip -> head) from its seating plane:
the head is at t > 0 and the shank runs down to t = -length.
"""
from __future__ import annotations

import math
import re

import numpy as np
from build123d import Plane, Solid

import assembly_geometry as ag
import hardware
import print3d_profile
from print3d_assembly import Bolted, NutPocket

HEAD_RAYS = 12  # rays round the axis that find the surface under a head
FLUSH_REACH = 0.5  # mm, a countersunk head's rim surface is probed this far outside the head
PARALLEL_TOL = 1.0  # degrees, a nut axis within this of the screw axis counts as parallel


def _num(value, digits=3):
    """A JSON-ready float, or None for a missing or NaN value."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return round(float(value), digits) + 0.0  # + 0.0: no -0.0


def _fmt(value):
    """A short number for messages: 0.52, 5.1, 3."""
    return f"{round(float(value), 2):g}"


def _type_name(iface):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", type(iface).__name__).lower()


def _result(iface, names, failures, measured, expected, location, planes, passed):
    return {
        "name": iface.name,
        "type": _type_name(iface),
        "items": list(names),
        "status": "fail" if failures else "pass",
        "measured": measured,
        "expected": expected,
        "source": iface.source,
        "location": [_num(v, 4) for v in location],
        "message": "; ".join(failures) if failures else passed,
        "evidence": [],
        "planes": [([_num(v, 4) for v in point], [_num(v, 6) for v in normal]) for point, normal in planes],
    }


def _sorted_failures(failures):
    """"not found" first, so a missing feature leads the message."""
    return [f for f in failures if f.startswith("not found:")] + [f for f in failures if not f.startswith("not found:")]


def check_interface(iface, items) -> dict:
    """Check one declared interface on the placed items {name: PlacedItem}."""
    checks = {Bolted: _check_bolted, NutPocket: _check_nut_pocket}
    check = checks.get(type(iface))
    if check is None:
        raise NotImplementedError(f"no check for {type(iface).__name__} interfaces yet")
    return check(iface, items)


# --- bolted -------------------------------------------------------------------------------------------------------


def bolt_search_r(size, role):
    """search_r for the hole probes of a bolted joint: the clearance hole's diameter (an insert role: the larger of
    it and the insert hole's).

    That measures holes up to twice the clearance diameter (an oversized hole is measured, not missed), and keeps
    the region whose walls are looked at (1.9 x search_r, about 6.8 mm for M3) close to the hole. Material near the
    axis that isn't a hole at some depths (a skirt beside the hole) is excluded by hole_on_axis, not fatal.
    """
    search_r = print3d_profile.screw_clearance(size)
    if role == "insert":
        search_r = max(search_r, print3d_profile.insert_hole(size))
    return search_r


def _nut_height(decl):
    return print3d_profile.ISO4032_M[decl.size] if decl.kind == "hex" else hardware.NYLOC_H[decl.size]


def _head_and_shank(decl):
    """The head (socket/button: the head cylinder; countersunk: the cone) and the shank, in the screw's local frame."""
    d = float(decl.size[1:])
    head = hardware.HEADS[decl.kind][decl.size]
    if decl.kind == "countersunk":
        depth = (head["dk"] - d) / 2
        cone = Solid.make_cone(d / 2, head["dk"] / 2, depth, Plane.XY.offset(-depth))
        shank_top = -depth
        head_solid = cone
    else:
        head_solid = Solid.make_cylinder(head["dk"] / 2, head["k"])
        shank_top = 0.0
    shank = Solid.make_cylinder(d / 2, decl.length + shank_top, Plane.XY.offset(-decl.length))
    return head_solid, shank


def _ring(frame, t, radius):
    """HEAD_RAYS points on a circle of this radius round the axis, at t along it."""
    points = []
    for k in range(HEAD_RAYS):
        angle = 2 * math.pi * k / HEAD_RAYS
        offset = frame.x_dir * (radius * math.cos(angle)) + frame.y_dir * (radius * math.sin(angle))
        points.append(frame.origin + frame.z_dir * t + offset)
    return points


def _first_surface(frame, t_start, radius, parts):
    """The smallest distance down the axis (towards the tip) from t_start to a surface of parts, on a ring of rays:
    (distance, part name), or (None, None) when no ray meets a part."""
    down = tuple(-frame.z_dir)
    best = (None, None)
    for start in _ring(frame, t_start, radius):
        for name, shape in parts:
            hit = ag.probe(shape, tuple(start), down)
            if hit is not None and (best[0] is None or hit < best[0]):
                best = (hit, name)
    return best


def _check_bolted(iface, items):
    screw = items[iface.screw]
    decl = screw.decl
    fast = hardware.fastener(decl.size, decl.kind, decl.length)
    origin, direction = screw.axis()
    frame = Plane(screw.loc)
    axis_o, axis_d = np.array(origin), np.array(direction)
    limit = iface.limit or ag.AXIS_LIMIT
    clearance = print3d_profile.screw_clearance(decl.size)
    head = hardware.HEADS[decl.kind][decl.size]
    failures = []
    measured = {"offset": {}, "diameter": {}, "hole_span": {}, "excluded": {}, "search_r": {}}
    expected = {"offset_max": limit, "diameter": {}}

    # (a) a hole on the axis in every part, (b) of a diameter that suits its role
    first_hole = None
    for name, role in iface.through:
        search_r = bolt_search_r(decl.size, role)
        found = ag.hole_on_axis(items[name].shape, origin, direction, search_r=search_r)
        measured["search_r"][name] = _num(search_r)
        measured["offset"][name] = _num(found.offset) if found.found else None
        measured["diameter"][name] = _num(found.diameter) if found.found else None
        measured["hole_span"][name] = [_num(v) for v in found.depth_span] if found.found else None
        measured["excluded"][name] = [[_num(lo), _num(hi), why] for lo, hi, why in found.excluded]
        if role == "clearance":
            expected["diameter"][name] = {"min": _num(clearance - ag.AXIS_LIMIT)}
        elif role == "insert":
            expected["diameter"][name] = {"nominal": print3d_profile.insert_hole(decl.size), "tol": ag.AXIS_LIMIT}
        else:
            expected["diameter"][name] = None  # a thread role's pilot hole isn't sized here
        if not found.found:
            failures.append(f"not found: no hole on the screw axis in {name} ({found.reason})")
            continue
        if first_hole is None:
            first_hole = found.depth_span
        if found.offset > limit:
            failures.append(f"{name}: hole offset {_fmt(found.offset)} mm from the screw axis (limit {_fmt(limit)})")
        if role == "clearance" and found.diameter < clearance - ag.AXIS_LIMIT:
            failures.append(
                f"{name}: clearance hole diameter {_fmt(found.diameter)} mm, needs at least "
                f"{_fmt(clearance - ag.AXIS_LIMIT)} (screw_clearance('{decl.size}') = {_fmt(clearance)})"
            )
        if role == "insert":
            nominal = print3d_profile.insert_hole(decl.size)
            if abs(found.diameter - nominal) > ag.AXIS_LIMIT:
                failures.append(
                    f"{name}: insert hole diameter {_fmt(found.diameter)} mm, expected {_fmt(nominal)} "
                    f"+/- {_fmt(ag.AXIS_LIMIT)} (insert_hole('{decl.size}'))"
                )

    # (c) the head seats
    head_local, shank_local = _head_and_shank(decl)
    head_solid = head_local.moved(screw.loc)
    parts = [(name, items[name].shape) for name, _ in iface.through]
    head_overlap = {name: _num(ag.overlap(head_solid, shape)[0]) for name, shape in parts}
    measured["head_overlap_mm3"] = head_overlap
    expected["overlap_max"] = ag.OVERLAP_EPS
    for name, volume in head_overlap.items():
        if volume > 0:
            failures.append(f"{iface.screw}: head overlaps {name} by {_fmt(volume)} mm^3")
    if decl.kind == "countersunk":
        _countersunk_seat(iface, decl, frame, head, head_solid, parts, measured, expected, failures)
    else:
        ring_r = (clearance / 2 + head["dk"] / 2) / 2  # between the hole's edge and the head's
        hit, _ = _first_surface(frame, fast.head_top, ring_r, parts)
        gap = None if hit is None else hit - fast.head_top
        measured["head_gap"] = _num(gap)
        expected["head_gap_max"] = ag.CONTACT_LIMIT
        if gap is None:
            failures.append(f"not found: no part surface under the head of {iface.screw}")
        elif gap > ag.CONTACT_LIMIT:
            failures.append(
                f"{iface.screw}: head {_fmt(gap)} mm above the part surface (limit {_fmt(ag.CONTACT_LIMIT)})"
            )

    # (d) length past the nut
    if iface.nut is not None:
        _protrusion(iface, decl, fast, items[iface.nut], axis_o, axis_d, limit, measured, expected, failures)

    # (e) the shank clears everything but the nut and the parts it threads into
    skip = {iface.screw, iface.nut} | {name for name, role in iface.through if role in ("thread", "insert")}
    shank = shank_local.moved(screw.loc)
    shank_overlap = {}
    for name, item in items.items():
        if name in skip:
            continue
        volume = ag.overlap(shank, item.shape)[0]
        if volume > 0:
            shank_overlap[name] = _num(volume)
            failures.append(f"{iface.screw}: shank overlaps {name} by {_fmt(volume)} mm^3")
    measured["shank_overlap_mm3"] = shank_overlap

    location = axis_o - axis_d * (decl.length / 2)
    across_t = (first_hole[0] + first_hole[1]) / 2 if first_hole else -min(1.0, decl.length / 2)
    planes = [(tuple(location), tuple(frame.y_dir)), (tuple(axis_o + axis_d * across_t), tuple(axis_d))]
    names = [iface.screw, *(name for name, _ in iface.through), *([iface.nut] if iface.nut else [])]
    through = ", ".join(name for name, _ in iface.through)
    passed = f"M{decl.size[1:]}x{_fmt(decl.length)} {decl.kind} through {through}: holes on axis, head seated"
    if iface.nut:
        passed += f", {_fmt(measured['protrusion'])} mm past the nut"
    return _result(iface, names, _sorted_failures(failures), measured, expected, location, planes, passed)


def _countersunk_seat(iface, decl, frame, head, cone, parts, measured, expected, failures):
    """The cone seats on its countersink (touching, no overlap) and its top is flush with the part's surface."""
    distances = {name: ag.min_distance(cone, shape) for name, shape in parts}
    sunk = min(distances, key=distances.get)  # the countersunk part: the one the cone sits in
    measured["head_gap"] = _num(distances[sunk])
    expected["head_gap_max"] = ag.CONTACT_LIMIT
    if distances[sunk] > ag.CONTACT_LIMIT:
        failures.append(
            f"{iface.screw}: countersunk head {_fmt(distances[sunk])} mm from the countersink in {sunk} "
            f"(limit {_fmt(ag.CONTACT_LIMIT)})"
        )
    reach = head["dk"] / 2 + FLUSH_REACH
    start = head["k"] + 1.0  # above the head's top face (t = 0)
    hit, _ = _first_surface(frame, start, reach, [(sunk, dict(parts)[sunk])])
    flush = None if hit is None else start - hit  # the part surface's t: > 0 the head is sunk, < 0 it stands proud
    measured["head_flush"] = _num(flush)
    expected["head_flush_max"] = ag.CONTACT_LIMIT
    if flush is None:
        failures.append(f"not found: no surface of {sunk} round the countersunk head of {iface.screw}")
    elif abs(flush) > ag.CONTACT_LIMIT:
        where = "below" if flush > 0 else "above"
        failures.append(
            f"{iface.screw}: countersunk head top {_fmt(abs(flush))} mm {where} the surface of {sunk} "
            f"(limit {_fmt(ag.CONTACT_LIMIT)})"
        )


def _protrusion(iface, decl, fast, nut, axis_o, axis_d, limit, measured, expected, failures):
    """How far the tip passes the nut's far face (the face away from the head), along the screw axis."""
    nut_o, nut_d = (np.array(v) for v in nut.axis())
    rel = nut_o - axis_o
    t0 = float(rel @ axis_d)
    off_axis = float(np.linalg.norm(rel - t0 * axis_d))
    tilt = math.degrees(math.acos(min(1.0, abs(float(nut_d @ axis_d)))))
    measured["nut_axis_offset"] = _num(off_axis)
    if off_axis > limit or tilt > PARALLEL_TOL:
        failures.append(
            f"{iface.nut}: nut axis {_fmt(off_axis)} mm from the screw axis, {_fmt(tilt)} deg off parallel "
            f"(limit {_fmt(limit)} mm)"
        )
    t1 = t0 + _nut_height(nut.decl) * float(nut_d @ axis_d)
    far = min(t0, t1)
    protrusion = far - fast.tip
    rule = iface.protrusion
    if rule is None:
        rule = fast.pitch if nut.decl.kind == "nyloc" else 0.0  # see design-rules.md
    measured["protrusion"] = _num(protrusion)
    expected["protrusion_min"] = rule
    if protrusion < 0:
        failures.append(f"screw too short: tip {_fmt(-protrusion)} mm short of the nut's far face")
    elif protrusion < rule:
        failures.append(
            f"screw too short: it protrudes {_fmt(protrusion)} mm past the nut's far face, needs {_fmt(rule)}"
        )


# --- nut pocket ---------------------------------------------------------------------------------------------------


def _check_nut_pocket(iface, items):
    nut, part = items[iface.nut], items[iface.part]
    decl = nut.decl
    limit = iface.limit or ag.AXIS_LIMIT
    origin, direction = (np.array(v) for v in nut.axis())
    frame = Plane(nut.loc)
    s = print3d_profile.ISO4032_AF[decl.size]
    corners = s / math.cos(math.radians(30))
    mid = origin + direction * (_nut_height(decl) / 2)
    failures = []

    volume = ag.overlap(nut.shape, part.shape)[0]
    if volume > 0:
        failures.append(f"{iface.nut}: the nut overlaps {iface.part} by {_fmt(volume)} mm^3")
    pocket = ag.pocket_section(part.shape, tuple(mid), tuple(direction), corners)
    measured = {
        "overlap_mm3": _num(volume),
        "pocket_width": _num(pocket.min_width) if pocket.found else None,
        "offset": _num(pocket.offset) if pocket.found else None,
        "closed": pocket.closed if pocket.found else None,
    }
    expected = {"overlap_max": ag.OVERLAP_EPS, "pocket_width_max": _num(corners), "nut_across_flats": s,
                "offset_max": limit}
    if not pocket.found:
        failures.append(f"not found: no pocket round the nut axis in {iface.part} ({pocket.reason})")
    else:
        if pocket.min_width >= corners:
            failures.append(
                f"{iface.part}: the pocket's smallest width {_fmt(pocket.min_width)} mm is not below the nut's "
                f"across-corners {_fmt(corners)} mm: the nut can turn"
            )
        if pocket.offset > limit:
            failures.append(
                f"{iface.nut}: nut axis {_fmt(pocket.offset)} mm from the pocket centre (limit {_fmt(limit)})"
            )
    planes = [(tuple(mid), tuple(direction)), (tuple(mid), tuple(frame.y_dir))]
    passed = f"{decl.size} {decl.kind} nut in {iface.part}: no overlap, can't turn, on the pocket centre"
    return _result(iface, [iface.nut, iface.part], _sorted_failures(failures), measured, expected, mid, planes,
                   passed)
