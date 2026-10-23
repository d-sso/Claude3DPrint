"""print3d assembly checks: one function per interface type, on PlacedItems in one common frame.

check_interface(iface, items) returns a JSON-ready dict:
  name, type, items (names), status ("pass"/"fail"), measured, expected, source, location [x, y, z], message,
  evidence (filled in by the sheets), planes [(point, normal)] for the evidence sections.
A feature that can't be found fails with a message starting "not found:"; a check never passes on nothing.

Screw-axis positions are reported as t, the distance along the screw's +Z (tip -> head) from its seating plane:
the head is at t > 0 and the shank runs down to t = -length.

Bolted holes are looked for only within the screw's extent (t from the tip to 0): material beyond the tip (a blind
hole's floor, a part further down the axis) neither blocks the screw nor counts as its hole. A thread or insert
part's hole must start at least ENGAGE_PITCHES x pitch before the tip; in a thread part only depths where the hole is
narrower than the screw's major diameter (the pilot) count. A nut must sit on the shank, and the depths where it sits
are not measured as any part's hole (that is its pocket, checked by NutPocket). A declared washer takes the head's
seat (head on washer, washer on the part) and is left out of the shank's overlap check.

Snaps are measured in 2D, in the snap plane's own frame: u across (perpendicular to insert_dir in the plane), v along
the pull-out direction (-insert_dir), both from the declared point. Only material within SNAP_WINDOW of the point
counts, so the other snaps of the same parts don't. The bump region is the band of v where the bump part's section
meets the catch's while it is pulled out; the running clearance is measured within CLEAR_MARGIN beside that band.

A passage's stand-in is taken as straight: its run line goes through its bounding-box centre along the declared
direction (or its longest bounding-box axis). In each part, one opening the line passes through must lie within the
stand-in's extent (assembly_geometry.passage_opening), else "not found".

overlap_sweep(items, intended) reports every overlapping pair not in `intended` (see intended_overlaps), and
check_target(target, items) measures one intent target; a target without a value fails with "no target:".
"""
from __future__ import annotations

import math
import re

import numpy as np
from build123d import Box, Compound, Location, Plane, Pos, Solid

import assembly_geometry as ag
import hardware
import print3d_profile
from print3d_assembly import Bolted, Clear, Gap, NutPocket, Passage, Position, Seat, Size, Snap, Step

HEAD_RAYS = 12  # rays round the axis that find the surface under a head
FLUSH_REACH = 0.5  # mm, a countersunk head's rim surface is probed this far outside the head
PARALLEL_TOL = 1.0  # degrees, a nut axis within this of the screw axis counts as parallel
ENGAGE_PITCHES = 2.0  # a thread or insert part's hole must reach at least this many pitches past its start
SNAP_STEP = 0.05  # mm, the bump part is pulled out of its catch in steps this long
SNAP_WINDOW = 3.0  # mm, snap sections count within this of the point (across, and along beyond the travel)
CLEAR_MARGIN = 1.0  # mm, a snap's running clearance is measured within this beside the bump band
SEAT_SLAB = 2.0  # mm, a seat's overlap is measured in a slab this thick about its plane
SEAT_REACH = 5.0  # mm, ... within this of its point in the plane, and its surfaces this far along the normal
PASSAGE_REACH = 1.0  # mm, a passage's opening probe looks this much beyond the stand-in plus its clearance
MIN_AREA = 1e-6  # mm^2, smaller section pieces are touching edges, not overlaps


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
    checks = {Bolted: _check_bolted, NutPocket: _check_nut_pocket, Snap: _check_snap, Seat: _check_seat,
              Passage: _check_passage, Clear: _check_clear}
    return checks[type(iface)](iface, items)


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


def _first_surface(frame, t_start, radius, parts, up=False):
    """The smallest distance down the axis (towards the tip; up=True: towards the head) from t_start to a surface of
    parts, on a ring of rays: (distance, part name), or (None, None) when no ray meets a part."""
    down = tuple(frame.z_dir if up else -frame.z_dir)
    best = (None, None)
    for start in _ring(frame, t_start, radius):
        for name, shape in parts:
            hit = ag.probe(shape, tuple(start), down)
            if hit is not None and (best[0] is None or hit < best[0]):
                best = (hit, name)
    return best


def _on_axis(item, height, axis_o, axis_d):
    """A nut's or washer's (t_lo, t_hi) along the screw axis, its axis's offset from the screw axis, and its tilt in
    degrees."""
    origin, direction = (np.array(v) for v in item.axis())
    rel = origin - axis_o
    t0 = float(rel @ axis_d)
    off_axis = float(np.linalg.norm(rel - t0 * axis_d))
    tilt = math.degrees(math.acos(min(1.0, abs(float(direction @ axis_d)))))
    t1 = t0 + height * float(direction @ axis_d)
    return (min(t0, t1), max(t0, t1)), off_axis, tilt


def _nut_skip(iface, items, axis_o, axis_d, limit):
    """The depths where a coaxial nut sits, as a hole_on_axis skip range (none for an off-axis nut: fail safe)."""
    if iface.nut is None:
        return ()
    nut = items[iface.nut]
    span, off_axis, tilt = _on_axis(nut, _nut_height(nut.decl), axis_o, axis_d)
    if off_axis > limit or tilt > PARALLEL_TOL:
        return ()
    return ((*span, "not measured: the nut sits here"),)


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
    shank_span = (fast.tip, 0.0)  # only the screw's own extent along the axis counts: tip to seating plane
    skip = _nut_skip(iface, items, axis_o, axis_d, limit)
    for name, role in iface.through:
        search_r = bolt_search_r(decl.size, role)
        found = ag.hole_on_axis(items[name].shape, origin, direction, search_r=search_r, span=shank_span, skip=skip)
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
        if role in ("thread", "insert"):
            need = ENGAGE_PITCHES * fast.pitch
            if role == "thread":  # only the pilot engages: depths narrower than the screw's major diameter
                pilot = [hi - lo for lo, hi, _, diameter, _ in found.depths if diameter < _major_d(decl)]
                engaged = sum(pilot)
            else:  # the tip must run past the start of the hole (its head-side end) by ENGAGE_PITCHES x pitch
                pilot, engaged = None, found.depth_span[1] - fast.tip
            measured.setdefault("engagement", {})[name] = _num(engaged)
            expected.setdefault("engagement_min", {})[name] = _num(need)
            if pilot == []:
                failures.append(
                    f"not found: no pilot hole smaller than {decl.size} in {name} within the screw's extent "
                    f"(t {_fmt(fast.tip)} to 0): its hole there is {_fmt(found.diameter)} mm or wider"
                )
            elif engaged < need:
                failures.append(
                    f"{name}: screw engages {_fmt(engaged)} mm of the {role} hole, needs {_fmt(need)} "
                    f"({_fmt(ENGAGE_PITCHES)} x pitch {_fmt(fast.pitch)})"
                )

    # (c) the head seats: on the parts, or on its washer
    head_local, shank_local = _head_and_shank(decl)
    head_solid = head_local.moved(screw.loc)
    parts = [(name, items[name].shape) for name, _ in iface.through]
    under = [(iface.washer, items[iface.washer].shape)] if iface.washer else []
    head_overlap = {name: _num(ag.overlap(head_solid, shape)[0]) for name, shape in parts + under}
    measured["head_overlap_mm3"] = head_overlap
    expected["overlap_max"] = ag.OVERLAP_EPS
    for name, volume in head_overlap.items():
        if volume > 0:
            failures.append(f"{iface.screw}: head overlaps {name} by {_fmt(volume)} mm^3")
    if decl.kind == "countersunk":
        if iface.washer:
            failures.append(f"{iface.screw}: a countersunk head doesn't seat on a washer ({iface.washer})")
        _countersunk_seat(iface, decl, frame, head, head_solid, parts, measured, expected, failures)
    else:
        inner = clearance / 2
        if iface.washer:
            inner = max(inner, hardware.WASHERS[items[iface.washer].decl.size]["d1"] / 2)
        ring_r = (inner + head["dk"] / 2) / 2  # between the hole's (or washer bore's) edge and the head's
        hit, _ = _first_surface(frame, fast.head_top, ring_r, under or parts)
        gap = None if hit is None else hit - fast.head_top
        measured["head_gap"] = _num(gap)
        expected["head_gap_max"] = ag.CONTACT_LIMIT
        surface = f"washer {iface.washer}" if iface.washer else "the part surface"
        if gap is None:
            failures.append(f"not found: no surface of {surface} under the head of {iface.screw}")
        elif gap > ag.CONTACT_LIMIT:
            failures.append(
                f"{iface.screw}: head {_fmt(gap)} mm above {surface} (limit {_fmt(ag.CONTACT_LIMIT)})"
            )
    _washers(iface, items, frame, axis_o, axis_d, limit, clearance, parts, measured, expected, failures)

    # (d) length past the nut
    if iface.nut is not None:
        _protrusion(iface, decl, fast, items[iface.nut], axis_o, axis_d, limit, measured, expected, failures)

    # (e) the shank clears everything but the nut, its washers and the parts it threads into
    skip = {iface.screw, iface.nut, iface.washer, iface.nut_washer}
    skip |= {name for name, role in iface.through if role in ("thread", "insert")}
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
    names = [iface.screw, *(name for name, _ in iface.through),
             *(name for name in (iface.washer, iface.nut, iface.nut_washer) if name)]
    through = ", ".join(name for name, _ in iface.through)
    passed = f"M{decl.size[1:]}x{_fmt(decl.length)} {decl.kind} through {through}: holes on axis, head seated"
    if iface.nut:
        passed += f", {_fmt(measured['protrusion'])} mm past the nut"
    return _result(iface, names, _sorted_failures(failures), measured, expected, location, planes, passed)


def _major_d(decl):
    """The screw's major (nominal) diameter."""
    return float(decl.size[1:])


def _washers(iface, items, frame, axis_o, axis_d, limit, clearance, parts, measured, expected, failures):
    """Each declared washer is coaxial, overlaps no part, and touches the part surface it sits on: under a head
    washer (towards the tip) or above a nut washer (towards the head)."""
    measured["washers"] = {}
    for name, side in ((iface.washer, "head"), (iface.nut_washer, "nut")):
        if name is None:
            continue
        washer = items[name]
        size = hardware.WASHERS[washer.decl.size]
        (t_lo, t_hi), off_axis, tilt = _on_axis(washer, size["h"], axis_o, axis_d)
        overlaps = {part: _num(ag.overlap(washer.shape, shape)[0]) for part, shape in parts}
        ring_r = (max(clearance / 2, size["d1"] / 2) + size["d2"] / 2) / 2  # between the hole's edge and its rim
        if side == "head":
            hit, _ = _first_surface(frame, t_lo, ring_r, parts)
        else:
            hit, _ = _first_surface(frame, t_hi, ring_r, parts, up=True)
        measured["washers"][name] = {"side": side, "span": [_num(t_lo), _num(t_hi)], "axis_offset": _num(off_axis),
                                     "gap": _num(hit), "overlap_mm3": overlaps}
        if off_axis > limit or tilt > PARALLEL_TOL:
            failures.append(
                f"{name}: washer axis {_fmt(off_axis)} mm from the screw axis, {_fmt(tilt)} deg off parallel "
                f"(limit {_fmt(limit)} mm)"
            )
        for part, volume in overlaps.items():
            if volume > 0:
                failures.append(f"{name}: washer overlaps {part} by {_fmt(volume)} mm^3")
        if hit is None:
            where = "under" if side == "head" else "above"
            failures.append(f"not found: no part surface {where} washer {name} of {iface.screw}")
        elif hit > ag.CONTACT_LIMIT:
            failures.append(
                f"{name}: washer {_fmt(hit)} mm from the part surface (limit {_fmt(ag.CONTACT_LIMIT)})"
            )
    if measured["washers"]:
        expected["washer_gap_max"] = ag.CONTACT_LIMIT


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
    (far, near), off_axis, tilt = _on_axis(nut, _nut_height(nut.decl), axis_o, axis_d)
    measured["nut_axis_offset"] = _num(off_axis)
    if off_axis > limit or tilt > PARALLEL_TOL:
        failures.append(
            f"{iface.nut}: nut axis {_fmt(off_axis)} mm from the screw axis, {_fmt(tilt)} deg off parallel "
            f"(limit {_fmt(limit)} mm)"
        )
    measured["nut_span"] = [_num(far), _num(near)]
    if near > ag.RAY_TOL:  # a nut beyond the tip is "too short" below; one above the seating plane isn't on the shank
        failures.append(
            f"{iface.nut}: the nut is not on the shank: it runs from {_fmt(far)} to {_fmt(near)} mm along the "
            f"screw axis, the shank from {_fmt(fast.tip)} to 0"
        )
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



# --- snap ---------------------------------------------------------------------------------------------------------


def _rect(u0, u1, v0, v1, depth=1.0):
    """A box over u0..u1 x v0..v1 in a plane's local frame, `depth` thick about the plane."""
    return Pos((u0 + u1) / 2, (v0 + v1) / 2, 0) * Box(u1 - u0, v1 - v0, depth)


def _faces(pieces):
    return [face for piece in pieces for face in piece.faces() if face.area > MIN_AREA]


def _clip(faces, region):
    """The parts of these faces inside region (a solid, or a coplanar face)."""
    return _faces(p for face in faces if ag._boxes_meet(face, region) for p in ag._pieces(face.intersect(region)))


def _snap_frame(iface):
    """The snap plane's frame: x across (perpendicular to insert_dir), y the pull-out direction, z the normal."""
    pull = -np.array(iface.insert_dir)
    across = np.cross(pull, np.array(iface.normal))
    return Plane(origin=iface.point, x_dir=tuple(across), z_dir=iface.normal)


def _pull_out(bump, catch, travel):
    """Pull the bump faces out along +v in SNAP_STEPs over travel: the largest overlap width across with the catch
    faces, and the v band (in the assembled position) where they met, or None when they never meet."""
    top = max(face.bounding_box().max.Y for face in catch)
    width, band = 0.0, None
    for k in range(int(round(travel / SNAP_STEP)) + 1):
        s = k * SNAP_STEP
        moved = [face.moved(Location((0, s, 0))) for face in bump]
        if min(face.bounding_box().min.Y for face in moved) > top:
            break  # the bump is past the whole catch
        for piece in (p for face in moved for c in catch for p in _clip([face], c)):
            box = piece.bounding_box()
            width = max(width, box.size.X)
            lo, hi = box.min.Y - s, box.max.Y - s
            band = (lo, hi) if band is None else (min(band[0], lo), max(band[1], hi))
    return width, band


def _running_clearance(bump, catch, band):
    """The smallest distance between bump and catch faces within CLEAR_MARGIN beside the band, or None."""
    lo, hi = band
    best = None
    for region in (_rect(-SNAP_WINDOW, SNAP_WINDOW, lo - CLEAR_MARGIN, lo),
                   _rect(-SNAP_WINDOW, SNAP_WINDOW, hi, hi + CLEAR_MARGIN)):
        for fa in _clip(bump, region):
            for fb in _clip(catch, region):
                d = fa.distance_to(fb)
                best = d if best is None else min(best, d)
    return best


def _bump_overlap(bump, catch, frame, band):
    """The volume of the two parts' assembled overlap that lies in this snap's bump region."""
    lo, hi = band
    reach = bump.bounding_box().diagonal + catch.bounding_box().diagonal
    region = _rect(-SNAP_WINDOW, SNAP_WINDOW, lo - CLEAR_MARGIN, hi + CLEAR_MARGIN, depth=2 * reach)
    shared = [p for p in ag._pieces(bump.intersect(catch)) if p.volume > 0]
    if not shared:
        return 0.0
    inside = ag._pieces(Compound(shared).intersect(region.moved(Location(frame))))
    return sum(p.volume for p in inside if p.volume > 0)


def _range_failure(what, value, bounds):
    if bounds is not None and not bounds[0] <= value <= bounds[1]:
        return f"{what} {_fmt(value)} mm, expected {_fmt(bounds[0])} to {_fmt(bounds[1])}"
    return None


def _check_snap(iface, items):
    bump, catch = items[iface.bump], items[iface.catch]
    frame = _snap_frame(iface)
    sections = {}
    for name, item, v_hi in ((iface.bump, bump, SNAP_WINDOW), (iface.catch, catch, iface.travel + SNAP_WINDOW)):
        faces = [frame.to_local_coords(face) for face in ag.section_faces(item.shape, iface.point, iface.normal)]
        sections[name] = _clip(faces, _rect(-SNAP_WINDOW, SNAP_WINDOW, -SNAP_WINDOW, v_hi))
    failures = [
        f"not found: no section of {name} within {_fmt(SNAP_WINDOW)} mm of the snap point"
        for name, faces in sections.items() if not faces
    ]
    fit = None if iface.fit is None else _num(print3d_profile.fit(iface.fit) / 2)
    expected = {"engagement": list(iface.engagement) if iface.engagement else None, "engagement_above": 0.0,
                "clearance": list(iface.clearance) if iface.clearance else None, "fit_per_side": fit,
                "overlap_max": ag.OVERLAP_EPS}
    volume = ag.overlap(bump.shape, catch.shape)[0]
    measured = {"engagement": None, "clearance": None, "overlap_mm3": _num(volume), "bump_overlap_mm3": 0.0,
                "bump_band": None}
    if volume > 0:
        failures.append(f"{iface.bump} overlaps {iface.catch} by {_fmt(volume)} mm^3")
    if all(sections.values()):
        width, band = _pull_out(sections[iface.bump], sections[iface.catch], iface.travel)
        measured["engagement"] = _num(width)
        measured["bump_band"] = None if band is None else [_num(band[0]), _num(band[1])]
        if width <= 0:
            failures.append(
                f"not found: no engagement: {iface.bump}'s section never meets {iface.catch}'s within "
                f"{_fmt(iface.travel)} mm of pulling out"
            )
        else:
            failures.append(_range_failure("engagement", width, iface.engagement))
        clearance = _running_clearance(sections[iface.bump], sections[iface.catch], band or (0.0, 0.0))
        measured["clearance"] = _num(clearance)
        if clearance is None:
            failures.append(f"not found: no running surfaces of {iface.bump} and {iface.catch} beside the bump")
        else:
            failures.append(_range_failure("clearance", clearance, iface.clearance))
        if volume > 0 and band is not None:
            measured["bump_overlap_mm3"] = _num(_bump_overlap(bump.shape, catch.shape, frame, band))
    failures = [f for f in failures if f]
    passed = (f"{iface.bump} snaps into {iface.catch}: engagement {_fmt(measured['engagement'] or 0)} mm, "
              f"clearance {_fmt(measured['clearance'] or 0)} mm")
    if fit is not None:
        passed += f" (fits.{iface.fit} / 2 = {_fmt(fit)})"
    return _result(iface, [iface.bump, iface.catch], _sorted_failures(failures), measured, expected, iface.point,
                   [(iface.point, iface.normal)], passed)


# --- seat, passage, clear -----------------------------------------------------------------------------------------


def _check_seat(iface, items):
    """The gap is measured along the normal through the point: each part's surface crossing nearest the point.
    (The smallest distance in the slab would find other surfaces beside the seat, e.g. a skirt's running clearance,
    and miss a lifted seat.) The overlap is measured in the slab, near the point."""
    limit = iface.limit or ag.CONTACT_LIMIT
    failures, nearest = [], {}
    for name in (iface.a, iface.b):
        hits = [t for t in ag._line_hits(items[name].shape, iface.point, iface.normal) if abs(t) <= SEAT_REACH]
        nearest[name] = min(hits, key=abs) if hits else None
        if not hits:
            failures.append(
                f"not found: no surface of {name} within {_fmt(SEAT_REACH)} mm of the seat point along its normal"
            )
    region = _rect(-SEAT_REACH, SEAT_REACH, -SEAT_REACH, SEAT_REACH, depth=SEAT_SLAB)
    region = region.moved(Location(Plane(origin=iface.point, z_dir=iface.normal)))
    near = [[p for p in ag._pieces(items[name].shape.intersect(region)) if p.volume > 0] for name in (iface.a, iface.b)]
    volume = ag.overlap(Compound(near[0]), Compound(near[1]))[0] if all(near) else 0.0
    measured = {"gap": None, "overlap_mm3": _num(volume)}
    if volume > 0:
        failures.append(f"{iface.a} overlaps {iface.b} at the seat by {_fmt(volume)} mm^3")
    if None not in nearest.values():
        gap = 0.0 if volume > 0 else abs(nearest[iface.a] - nearest[iface.b])
        measured["gap"] = _num(gap)
        if gap > limit:
            failures.append(f"{iface.a} is {_fmt(gap)} mm from {iface.b} at the seat (limit {_fmt(limit)})")
    expected = {"gap_max": limit, "overlap_max": ag.OVERLAP_EPS}
    passed = f"{iface.a} seats on {iface.b}"
    return _result(iface, [iface.a, iface.b], _sorted_failures(failures), measured, expected, iface.point,
                   [(iface.point, iface.normal)], passed)


def _longest_axis(box):
    axis = [0.0, 0.0, 0.0]
    axis[int(np.argmax(tuple(box.size)))] = 1.0
    return tuple(axis)


def passage_search_r(cross_x, cross_y, min_clearance):
    """search_r for a passage's opening probe: half the stand-in's cross-section diagonal, plus min_clearance and
    PASSAGE_REACH. An opening whose walls all lie further out than that reads as open (not found), so the reach
    keeps a loose opening measurable; the opening must still enclose the run line (hole_on_axis's rules)."""
    return math.hypot(cross_x, cross_y) / 2 + min_clearance + PASSAGE_REACH


def _run_line(iface, item):
    """The stand-in's run line: (centre, direction, (lo, hi) extent along it, cross-section (x, y) size)."""
    box = item.shape.bounding_box()
    centre = tuple(box.center())
    direction = iface.direction or _longest_axis(box)
    local = Plane(origin=centre, z_dir=direction).to_local_coords(item.shape).bounding_box()
    return centre, direction, (local.min.Z, local.max.Z), (local.size.X, local.size.Y)


def _check_passage(iface, items):
    """Stand-ins are taken as straight: one run line through the bounding-box centre along `direction`."""
    item = items[iface.item]
    centre, direction, extent, cross = _run_line(iface, item)
    search_r = passage_search_r(*cross, iface.min_clearance)
    failures = []
    measured = {"overlap_mm3": {}, "clearance": {}, "opening": {}, "aperture": {},
                "direction": [_num(v, 6) for v in direction], "extent": [_num(v) for v in extent],
                "search_r": _num(search_r)}
    for name in iface.through:
        part = items[name].shape
        if not ag._boxes_meet(item.shape, part):
            failures.append(f"not found: {iface.item} doesn't reach {name} (their bounding boxes don't meet)")
            measured["overlap_mm3"][name] = measured["clearance"][name] = None
            measured["opening"][name] = measured["aperture"][name] = None
            continue
        opening = ag.passage_opening(part, centre, direction, search_r=search_r, extent=extent)
        measured["opening"][name] = [_num(v) for v in opening.span] if opening.found else None
        measured["aperture"][name] = _num(opening.diameter) if opening.found else None
        if not opening.found:
            failures.append(f"not found: {iface.item} doesn't pass through an opening of {name} ({opening.reason}; "
                            f"depths are along its line from its centre)")
        volume = ag.overlap(item.shape, part)[0]
        clearance = 0.0 if volume > 0 else item.shape.distance_to(part)
        measured["overlap_mm3"][name], measured["clearance"][name] = _num(volume), _num(clearance)
        if volume > 0:
            failures.append(f"{iface.item} overlaps {name} by {_fmt(volume)} mm^3: {name} blocks it")
        elif clearance < iface.min_clearance:
            failures.append(f"{name}: clearance {_fmt(clearance)} mm, needs at least {_fmt(iface.min_clearance)}")
    expected = {"overlap_max": ag.OVERLAP_EPS, "clearance_min": iface.min_clearance}
    passed = f"{iface.item} passes through {', '.join(iface.through)} with at least {_fmt(iface.min_clearance)} mm"
    return _result(iface, [iface.item, *iface.through], _sorted_failures(failures), measured, expected, centre,
                   [(centre, direction)], passed)


def _closest(a, b):
    """(distance, midpoint, direction a -> b) between two shapes; an overlap gives 0 at its centre."""
    volume, centre = ag.overlap(a, b)
    if volume > 0:
        return 0.0, centre, (0.0, 0.0, 1.0)
    pa, pb = a.closest_points(b)
    gap = pb - pa
    direction = tuple(gap.normalized()) if gap.length > 0 else (0.0, 0.0, 1.0)
    return gap.length, tuple((pa + pb) * 0.5), direction


def _check_clear(iface, items):
    """Any overlap fails, whatever min_distance is (min_distance=0 means "no overlap, touching allowed")."""
    a, b = items[iface.a].shape, items[iface.b].shape
    volume = ag.overlap(a, b)[0]
    distance, location, direction = _closest(a, b)
    failures = []
    if volume > 0:
        failures.append(f"{iface.a} overlaps {iface.b} by {_fmt(volume)} mm^3")
    elif distance < iface.min_distance:
        failures.append(f"{iface.a} is {_fmt(distance)} mm from {iface.b}, needs at least {_fmt(iface.min_distance)}")
    passed = f"{iface.a} keeps {_fmt(distance)} mm from {iface.b}"
    return _result(iface, [iface.a, iface.b], failures, {"distance": _num(distance), "overlap_mm3": _num(volume)},
                   {"min_distance": iface.min_distance, "overlap_max": ag.OVERLAP_EPS}, location,
                   [(location, direction)], passed)


# --- overlap sweep ------------------------------------------------------------------------------------------------


def intended_overlaps(interfaces, results=None) -> set:
    """The item pairs whose overlap is intended, as frozensets of names.

    A screw with the parts it threads into (thread or insert role); a snap's two parts only when the snap results
    (`results`, {interface name: check_interface result}) of every snap between them put all of their overlap in
    the bump regions, i.e. the overlap is the snap interference (which the snap check reports). Nothing else.
    """
    results = results or {}
    pairs, snaps = set(), {}
    for iface in interfaces:
        if isinstance(iface, Bolted):
            pairs |= {frozenset((iface.screw, name)) for name, role in iface.through if role in ("thread", "insert")}
        elif isinstance(iface, Snap):
            snaps.setdefault(frozenset((iface.bump, iface.catch)), []).append(results.get(iface.name))
    for pair, found in snaps.items():
        if any(r is None for r in found):
            continue
        total = found[0]["measured"]["overlap_mm3"] or 0.0
        at_bumps = sum(r["measured"]["bump_overlap_mm3"] or 0.0 for r in found)
        if total > 0 and total - at_bumps <= ag.OVERLAP_EPS:
            pairs.add(pair)
    return pairs


def overlap_sweep(items, intended, pairs=None) -> list[dict]:
    """Every pair of items {name: PlacedItem} that overlaps, other than the intended pairs (see intended_overlaps).

    pairs: only these (a, b) name pairs are looked at (None: every pair of items, in their order).
    Each entry: a, b, volume_mm3, location (centre of the overlap's bounding box), size (that box's x, y, z),
    planes (through it, normals z and x), status "fail", message.
    """
    if pairs is None:
        names = list(items)
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
    entries = []
    for a, b in pairs:
        if frozenset((a, b)) in intended:
            continue
        volume, centre, size = ag.overlap_extent(items[a].shape, items[b].shape)  # boxes that don't meet: 0 at once
        if volume <= 0:
            continue
        point = [_num(v, 4) for v in centre]
        entries.append({
            "a": a,
            "b": b,
            "volume_mm3": _num(volume),
            "location": point,
            "size": [_num(v, 4) for v in size],
            "planes": [(point, [0.0, 0.0, 1.0]), (point, [1.0, 0.0, 0.0])],
            "status": "fail",
            "message": f"{a} overlaps {b} by {_fmt(volume)} mm^3",
        })
    return entries


# --- intent targets -----------------------------------------------------------------------------------------------


def _point(p):
    return "(" + ", ".join(_fmt(v) for v in p) + ")"


def _measure_size(target, items):
    boxes = [items[name].shape.bounding_box() for name in target.items]
    lo = [min(tuple(b.min)[k] for b in boxes) for k in range(3)]
    hi = [max(tuple(b.max)[k] for b in boxes) for k in range(3)]
    i = "xyz".index(target.axis)
    return list(target.items), hi[i] - lo[i], [(l + h) / 2 for l, h in zip(lo, hi)], None


def _measure_gap(target, items):
    a, b = items[target.a].shape, items[target.b].shape
    names = [target.a, target.b]
    if target.direction is None:
        distance, location, _ = _closest(a, b)
        return names, distance, location, None
    # signed, along direction: b's first crossing minus a's last, on the line through the middle between them
    d = np.array(target.direction)
    mid = (np.array(tuple(a.bounding_box().center())) + np.array(tuple(b.bounding_box().center()))) / 2
    hits = {name: ag._line_hits(shape, tuple(mid), tuple(d)) for name, shape in ((target.a, a), (target.b, b))}
    missed = [name for name, found in hits.items() if not found]
    if missed:
        line = f"the line along {_point(d)} through {_point(mid)}"
        return names, None, tuple(mid), f"{line} misses {missed[0]}"
    far_a, near_b = max(hits[target.a]), min(hits[target.b])
    return names, near_b - far_a, tuple(mid + d * (far_a + near_b) / 2), None


def _measure_step(target, items):
    names = list(dict.fromkeys((target.a, target.b)))  # one name when both faces are on the same item
    hits = []  # one per probe, in order: a and b may be the same item
    for name, origin in ((target.a, target.origin_a), (target.b, target.origin_b)):
        hits.append(ag.probe(items[name].shape, origin, target.direction))
        if hits[-1] is None:
            reason = f"the probe from {_point(origin)} along {_point(target.direction)} misses {name}"
            return names, None, origin, reason
    location = np.array(target.origin_a) + np.array(target.direction) * hits[0]
    return names, hits[0] - hits[1], tuple(location), None


def _measure_position(target, items):
    """The hit point's coordinate along the probe's line, signed so the line's largest component is positive
    (a probe along -x reports the x coordinate)."""
    d = np.array(target.direction)
    hit = ag.probe(items[target.item].shape, target.origin, target.direction)
    if hit is None:
        reason = f"the probe from {_point(target.origin)} along {_point(d)} misses {target.item}"
        return [target.item], None, target.origin, reason
    point = np.array(target.origin) + d * hit
    axis = d if d[np.argmax(np.abs(d))] > 0 else -d
    return [target.item], float(point @ axis), tuple(point), None


def _bounds_text(lo, hi):
    if lo is not None and hi is not None:
        return f"{_fmt(lo)} to {_fmt(hi)}"
    return f">= {_fmt(lo)}" if lo is not None else f"<= {_fmt(hi)}"


def check_target(target, items) -> dict:
    """Measure one intent target (Size, Gap, Step, Position) on the placed items and compare it with its target.

    The keys of an interface result, with `measured` the measured number in mm (None when not found), `value` and
    `tol` the target value and its tolerance. `expected` is the value, or {"min", "max"} for a bounded target
    (passes iff min <= measured <= max; an open end is None).
    """
    measure = {Size: _measure_size, Gap: _measure_gap, Step: _measure_step, Position: _measure_position}
    names, value, location, missing = measure[type(target)](target, items)
    lo, hi = target.min, target.max
    bounded = lo is not None or hi is not None
    failures = []
    if target.value is None and not bounded:
        measures = "" if value is None else f", the model measures {_fmt(value)} mm"
        failures.append(f"no target: {target.name} has no value{measures}; ask the user for it ({target.source})")
    if missing:
        failures.append(f"not found: {missing}")
    disagree = "the model and the document disagree; ask the user which is right"
    if target.value is not None and value is not None and abs(value - target.value) > target.tol:
        failures.append(
            f"measured {_fmt(value)} mm, target {_fmt(target.value)} +/- {_fmt(target.tol)} ({target.source}): "
            + disagree
        )
    kind = _type_name(target)
    if bounded and value is not None:
        if lo is not None and value < lo:
            failures.append(f"{kind} {_fmt(value)} mm is below the minimum {_fmt(lo)} ({target.source}): {disagree}")
        if hi is not None and value > hi:
            failures.append(f"{kind} {_fmt(value)} mm is above the maximum {_fmt(hi)} ({target.source}): {disagree}")
    if bounded:
        expected = {"min": lo, "max": hi}
        passed = f"measured {_fmt(value or 0)} mm, target {_bounds_text(lo, hi)}"
    else:
        expected = target.value
        passed = f"measured {_fmt(value or 0)} mm, target {_fmt(target.value or 0)} +/- {_fmt(target.tol)}"
    result = _result(target, names, failures, _num(value), expected, location, [], passed)
    result.update(value=target.value, tol=target.tol)
    return result
