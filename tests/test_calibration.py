import json
import os
import subprocess
import sys
import zipfile

import pytest
from conftest import REPO, SCRIPTS

import print3d_profile as pp
from print3d_profile import load_profile
from run_model import load_model

COUPON = REPO / "skills" / "print3d" / "calibration" / "calibration_coupon.py"


@pytest.fixture
def built():
    pp.activate(load_profile())
    module = load_model(COUPON)
    parts = module.build(dict(module.PARAMETERS))
    yield module, parts
    pp.activate(None)


def circle_radii(shape):
    from build123d import GeomType

    return {round(e.radius, 3) for e in shape.edges().filter_by(GeomType.CIRCLE)}


def test_two_valid_parts(built):
    _, parts = built
    assert set(parts) == {"coupon", "peg"}
    for shape in parts.values():
        assert shape.is_valid and shape.volume > 0


def test_coupon_plate_size_on_bed(built):
    _, parts = built
    bb = parts["coupon"].bounding_box()
    assert (round(bb.size.X, 3), round(bb.size.Y, 3)) == (100.0, 60.0)
    assert round(bb.min.Z, 3) == 0.0
    assert round(bb.max.Z, 3) == 15.0  # 5 mm plate + 10 mm fins


def test_raw_holes_are_exact_nominal(built):
    module, parts = built
    radii = circle_radii(parts["coupon"])
    for d in module.RAW_HOLES:
        assert round(d / 2, 3) in radii


def test_sockets_use_compensated_diameters(built):
    module, parts = built
    radii = circle_radii(parts["coupon"])
    for c in module.SOCKET_CLEARANCES:
        assert round(pp.hole(module.PEG_D + c) / 2, 3) in radii


def test_peg_is_raw_nominal_and_beside_coupon(built):
    module, parts = built
    peg = parts["peg"].bounding_box()
    assert round(peg.size.X, 3) == module.PEG_D and round(peg.size.Z, 3) == 10.0
    assert peg.min.X > parts["coupon"].bounding_box().max.X


def test_everything_fits_the_bed(built):
    _, parts = built
    from build123d import Compound

    bb = Compound(list(parts.values())).bounding_box()
    assert bb.size.X <= 256 - 10 and bb.size.Y <= 256 - 10 and bb.size.Z <= 256


def test_calibrate_command(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'parts_dir = "{(tmp_path / "parts").as_posix()}"\n', encoding="utf-8")
    env = {**os.environ, "PRINT3D_CONFIG": str(config)}
    proc = subprocess.run([sys.executable, str(SCRIPTS / "print3d.py"), "calibrate"],
                          capture_output=True, text=True, env=env)
    data = json.loads(proc.stdout)
    assert proc.returncode == 0, data
    out = tmp_path / "parts" / "calibration" / "centauri-carbon-pla-plus" / "out"
    assert (out / "calibration_coupon.3mf").is_file()
    with zipfile.ZipFile(out / "calibration_coupon.3mf") as z:
        model_xml = z.read("3D/3dmodel.model").decode("utf-8")
    assert 'name="coupon"' in model_xml and 'name="peg"' in model_xml
    assert isinstance(data["instructions"], list) and len(data["instructions"]) >= 4
    assert any("holes.compensation" in line for line in data["instructions"])
    assert any("label value" in line for line in data["instructions"])
    assert data["profile"]["name"] == "centauri-carbon-pla-plus"


def slice_faces(shape, z):
    """Cross-section faces of `shape` at height z: intersect with a 0.02 mm slab, keep the slab's bottom face."""
    from build123d import Align, Axis, Box, Location, Plane

    slab = Box(300, 300, 0.02, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(Location((0, 0, z)))
    return (shape & slab).faces().filter_by(Plane.XY).filter_by_position(Axis.Z, z - 0.011, z - 0.009)


def void_diameters(face):
    return sorted(round(w.bounding_box().size.X, 3) for w in face.inner_wires())


def void_centres(face):
    return {(round(w.bounding_box().center().X, 2), round(w.bounding_box().center().Y, 2)) for w in face.inner_wires()}


def test_sockets_are_blind_with_one_mm_floor(built):
    module, parts = built
    coupon = parts["coupon"]
    raw = sorted(module.RAW_HOLES)
    sockets = sorted(round(pp.hole(module.PEG_D + c), 3) for c in module.SOCKET_CLEARANCES)
    (inside,) = slice_faces(coupon, 3.0)
    assert void_diameters(inside) == sorted(raw + sockets)
    (below,) = slice_faces(coupon, 0.95)  # just under the 1 mm floor: raw holes only
    assert void_diameters(below) == raw
    (above,) = slice_faces(coupon, 1.05)  # just over the floor: sockets are open
    assert len(above.inner_wires()) == 12


def test_socket_and_hole_positions(built):
    module, parts = built
    (inside,) = slice_faces(parts["coupon"], 3.0)
    expected = {(x, 20.0) for x in module.HOLE_X}
    expected |= {(module.SOCKET_X[i % 4], module.SOCKET_ROWS_Y[i // 4]) for i in range(8)}
    assert void_centres(inside) == expected


def test_fins_are_thin_rectangles_ten_long(built):
    module, parts = built
    fins = slice_faces(parts["coupon"], 10.0)
    sizes = sorted((round(f.bounding_box().size.X, 2), round(f.bounding_box().size.Y, 2)) for f in fins)
    assert sizes == [(t, 10.0) for t in module.FIN_THICKNESSES]
    for f in fins:  # rectangles, not just bounding boxes
        bb = f.bounding_box()
        assert abs(f.area - bb.size.X * bb.size.Y) < 0.01
    assert len(slice_faces(parts["coupon"], 6.0)) == 4
    assert len(slice_faces(parts["coupon"], 14.9)) == 4


def test_labels_are_engraved(built):
    module, parts = built
    params = dict(module.PARAMETERS, label_depth=0.001)
    bare = module.build(params)["coupon"]
    assert bare.volume - parts["coupon"].volume > 1.0
    # engraving stays in the top 0.4 mm: the section just below it is unaffected
    assert len(slice_faces(parts["coupon"], 4.5)) == 1
    assert slice_faces(parts["coupon"], 4.5)[0].area == pytest.approx(slice_faces(bare, 4.5)[0].area, abs=0.01)
