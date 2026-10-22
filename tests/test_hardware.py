"""Tests for the hardware catalogue of fastener stand-ins."""
import pytest

import hardware
import print3d_profile as pp


def test_m3_values():
    assert hardware.HEADS["socket"]["M3"] == {"dk": 5.5, "k": 3.0}
    assert hardware.HEADS["countersunk"]["M3"] == {"dk": 6.0, "k": 1.7}
    assert hardware.HEADS["button"]["M3"] == {"dk": 5.7, "k": 1.65}
    assert pp.ISO4032_AF["M3"] == 5.5 and pp.ISO4032_M["M3"] == 2.4
    assert hardware.NYLOC_H["M3"] == 4.0
    assert hardware.WASHERS["M3"] == {"d1": 3.2, "d2": 7.0, "h": 0.5}
    assert hardware.PITCH["M3"] == 0.5


def test_nut_tables_match_profile():
    for size in pp.ISO4032_AF:
        if size not in hardware.PITCH:
            continue
        bb = hardware.nut(size, "hex").bounding_box()
        assert bb.min.Z == pytest.approx(0, abs=1e-6)
        assert bb.size.Z == pytest.approx(pp.ISO4032_M[size], abs=1e-6)
        # hex: across-corners in one axis, across-flats in the other
        af = min(bb.size.X, bb.size.Y)
        assert af == pytest.approx(pp.ISO4032_AF[size], abs=1e-3)
    for size in hardware.PITCH:
        assert size in pp.ISO4032_AF


def test_screw_geometry():
    bb = hardware.screw("M3", "socket", 10).bounding_box()
    assert (bb.min.Z, bb.max.Z) == (pytest.approx(-10), pytest.approx(3))
    assert bb.max.X == pytest.approx(2.75) and bb.min.Y == pytest.approx(-2.75)
    bb = hardware.screw("M3", "countersunk", 10).bounding_box()
    assert (bb.min.Z, bb.max.Z) == (pytest.approx(-10), pytest.approx(0, abs=1e-6))
    assert bb.size.X == pytest.approx(6.0)


def test_unknown_size_or_kind_lists_valid():
    with pytest.raises(ValueError, match="M2.5"):
        hardware.screw("M7", "socket", 10)
    with pytest.raises(ValueError, match="countersunk"):
        hardware.screw("M3", "pan", 10)
    with pytest.raises(ValueError, match="nyloc"):
        hardware.nut("M3", "wing")


def test_all_sizes_build_valid_solids():
    for kind in hardware.SCREW_KINDS:
        for size in hardware.HEADS[kind]:
            s = hardware.screw(size, kind, 12)
            assert s.is_valid and s.volume > 0
    for kind in hardware.NUT_KINDS:
        for size in hardware.PITCH:
            n = hardware.nut(size, kind)
            assert n.is_valid and n.volume > 0
    for size in hardware.WASHERS:
        w = hardware.washer(size)
        assert w.is_valid and w.volume > 0


def test_fastener_dataclass_fields():
    f = hardware.fastener("M3", "socket", 10)
    assert f.head_top == 3.0 and f.tip == -10 and f.pitch == 0.5
    assert hardware.fastener("M3", "countersunk", 10).head_top == 0.0
