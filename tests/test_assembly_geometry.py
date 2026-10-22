"""Exact measurement primitives for assembly checks, on simple build123d shapes."""
import math

import pytest
from build123d import Align, Box, Circle, Compound, Cylinder, Plane, Polygon, Pos, extrude

import assembly_geometry as ag

MIN_Z = (Align.CENTER, Align.CENTER, Align.MIN)


def plate(thickness=5.0, hole_at=None, hole_d=3.4, z0=0.0):
    """A 20 x 20 plate from z0 up, optionally with a vertical round hole at hole_at (x, y)."""
    body = Pos(0, 0, z0) * Box(20, 20, thickness, align=MIN_Z)
    if hole_at is not None:
        body -= Pos(hole_at[0], hole_at[1], z0) * Cylinder(hole_d / 2, thickness, align=MIN_Z)
    return body


def test_overlap_volume_of_two_boxes():
    volume, centre = ag.overlap(Box(10, 10, 10), Pos(8, 0, 0) * Box(10, 10, 10))
    assert volume == pytest.approx(200, abs=0.01)
    assert centre == pytest.approx((4, 0, 0), abs=1e-6)


def test_touching_faces_are_not_overlap():
    assert ag.overlap(Box(10, 10, 10), Pos(10, 0, 0) * Box(10, 10, 10)) == (0.0, None)
    seated = Pos(0, 0, 5) * Cylinder(3, 4, align=MIN_Z)
    assert ag.overlap(Box(10, 10, 10), seated) == (0.0, None)


def test_separate_boxes_are_not_overlap():
    assert ag.overlap(Box(10, 10, 10), Pos(30, 0, 0) * Box(10, 10, 10)) == (0.0, None)


def test_min_distance_gap():
    assert ag.min_distance(Box(10, 10, 10), Pos(10.5, 0, 0) * Box(10, 10, 10)) == pytest.approx(0.5, abs=1e-6)


def test_min_distance_overlap_is_zero():
    # one box inside the other: the boundaries are apart, but the solids overlap
    assert ag.min_distance(Box(10, 10, 10), Box(2, 2, 2)) == 0.0


def test_round_hole_on_axis():
    result = ag.hole_on_axis(plate(hole_at=(0, 0)), (0, 0, 10), (0, 0, -1), search_r=3)
    assert result.found
    assert result.reason == ""
    assert result.offset <= 0.02
    assert result.diameter == pytest.approx(3.4, abs=0.05)
    assert result.free_radius == pytest.approx(1.7, abs=0.05)
    assert result.samples >= 3
    assert result.depth_span == pytest.approx((5, 10), abs=0.05)  # along the axis from the origin


def test_round_hole_offset():
    result = ag.hole_on_axis(plate(hole_at=(0, 0)), (0.5, 0, 0), (0, 0, 1), search_r=3)
    assert result.found
    assert result.offset == pytest.approx(0.5, abs=0.05)
    assert result.diameter == pytest.approx(3.4, abs=0.05)
    assert result.free_radius == pytest.approx(1.2, abs=0.05)


def test_teardrop_hole_centred():
    r = 1.7
    a = r / math.sqrt(2)
    profile = Circle(r) + Polygon((-a, a), (a, a), (0, r * math.sqrt(2)), align=None)
    hole = extrude(Plane.YZ * profile, amount=10, both=True)  # along X, point towards +Z
    block = Box(10, 10, 10) - hole
    result = ag.hole_on_axis(block, (0, 0, 0), (1, 0, 0), search_r=3)
    assert result.found
    assert result.offset <= 0.05
    assert result.diameter == pytest.approx(3.4, abs=0.05)


def test_hole_not_found():
    result = ag.hole_on_axis(plate(), (0, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found


def test_nothing_near_axis_not_found():
    result = ag.hole_on_axis(plate(), (50, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found
    assert result.samples == 0


def test_axis_beside_plate_edge_not_found():
    # the axis runs 1 mm outside the plate's edge: material is within search_r, but there is no hole
    result = ag.hole_on_axis(plate(), (11, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found


def test_axis_blocked_partway_not_found():
    # the hole only goes half way: the axis point is inside material at the lower samples
    body = Box(20, 20, 10, align=MIN_Z) - Pos(0, 0, 5) * Cylinder(1.7, 5, align=MIN_Z)
    result = ag.hole_on_axis(body, (0, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found


def test_u_notch_round_end_is_hole():
    # a 3.4 wide U-slot from the plate's +X edge, its round end centred on the axis
    slot = Pos(0, 0, 0) * Cylinder(1.7, 5, align=MIN_Z) + Box(20, 3.4, 5, align=(Align.MIN, Align.CENTER, Align.MIN))
    result = ag.hole_on_axis(plate() - slot, (0, 0, 0), (0, 0, 1), search_r=3)
    assert result.found
    assert result.offset <= 0.05
    assert result.diameter == pytest.approx(3.4, abs=0.05)


def test_square_end_notch_is_hole():
    # a flat-ended 3.4 wide slot from the plate's +X edge: the material still wraps well past 180 degrees
    notch = Pos(-1.7, 0, 0) * Box(20, 3.4, 5, align=(Align.MIN, Align.CENTER, Align.MIN))
    result = ag.hole_on_axis(plate() - notch, (0, 0, 0), (0, 0, 1), search_r=3)
    assert result.found
    assert result.offset <= 0.05
    assert result.diameter == pytest.approx(3.4, abs=0.05)


def test_long_closed_slot_is_hole():
    # a closed slot longer than the search region, probed off its centreline: the centreline point nearest the axis
    ends = Pos(-4, 0, 0) * Cylinder(1.7, 5, align=MIN_Z) + Pos(4, 0, 0) * Cylinder(1.7, 5, align=MIN_Z)
    slot = ends + Box(8, 3.4, 5, align=MIN_Z)
    result = ag.hole_on_axis(plate() - slot, (1, 0.2, 0), (0, 0, 1), search_r=3)
    assert result.found
    assert result.offset == pytest.approx(0.2, abs=0.05)
    assert result.diameter == pytest.approx(3.4, abs=0.05)


def test_membrane_on_axis_blocks():
    # a 0.3 mm layer across the hole falls between the 1 mm samples; it still blocks the screw
    body = plate() - Cylinder(1.7, 2.5, align=MIN_Z) - Pos(0, 0, 2.8) * Cylinder(1.7, 2.2, align=MIN_Z)
    result = ag.hole_on_axis(body, (0, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found
    assert "blocked" in result.reason


def test_thin_cap_blocks():
    # a blind hole from below, closed by a 0.15 mm cap inside the top end inset
    body = plate() - Cylinder(1.7, 4.85, align=MIN_Z)
    result = ag.hole_on_axis(body, (0, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found
    assert "blocked" in result.reason


def test_gap_between_blocks_not_hole():
    blocks = Compound([Pos(-7, 0, 0) * Box(10, 20, 5, align=MIN_Z), Pos(7, 0, 0) * Box(10, 20, 5, align=MIN_Z)])
    result = ag.hole_on_axis(blocks, (0, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found
    assert "not a hole" in result.reason


def test_gap_between_pins_not_hole():
    pins = Compound([Pos(-3, 0, 0) * Cylinder(1, 5, align=MIN_Z), Pos(3, 0, 0) * Cylinder(1, 5, align=MIN_Z)])
    result = ag.hole_on_axis(pins, (0, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found
    assert "not a hole" in result.reason


def test_open_parallel_channel_not_hole():
    # fork prongs: a 3.4 wide channel whose closed end is 6 mm from the axis, open at the other end
    fork = Pos(0, -8, 0) * Box(20, 4, 5, align=MIN_Z)
    for x in (-5.85, 5.85):
        fork += Pos(x, 2, 0) * Box(8.3, 16, 5, align=MIN_Z)
    result = ag.hole_on_axis(fork, (0, 0, 0), (0, 0, 1), search_r=3)
    assert not result.found
    assert "not a hole" in result.reason


def test_axis_through_two_parts_each_probed():
    lower = plate(hole_at=(0, 0))
    upper = plate(hole_at=(0.3, 0), z0=5)
    offsets = [ag.hole_on_axis(p, (0, 0, 0), (0, 0, 1), search_r=3).offset for p in (lower, upper)]
    assert offsets == pytest.approx([0.0, 0.3], abs=0.05)


def test_probe_first_hit():
    box = Box(10, 10, 5, align=MIN_Z)
    assert ag.probe(box, (0, 0, 10), (0, 0, -1)) == pytest.approx(5.0, abs=1e-6)
    assert ag.probe(box, (50, 0, 10), (0, 0, -1)) is None
    assert ag.probe(box, (0, 0, 10), (0, 0, 1)) is None  # the box is behind the ray


def test_section_faces_of_plate_with_hole():
    faces = ag.section_faces(plate(hole_at=(0, 0)), (0, 0, 2.5), (0, 0, 1))
    assert len(faces) == 1
    assert len(faces[0].inner_wires()) == 1
    assert faces[0].area == pytest.approx(400 - math.pi * 1.7**2, abs=0.01)
    assert ag.section_faces(plate(), (0, 0, 50), (0, 0, 1)) == []
