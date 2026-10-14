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


def build_with(module, **params):
    return module.build(dict(module.PARAMETERS, **params))


def slice_faces(shape, z):
    """Cross-section faces of `shape` at height z: intersect with a 0.02 mm slab, keep the slab's bottom face."""
    from build123d import Align, Axis, Box, Location, Plane

    slab = Box(300, 300, 0.02, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(Location((0, 0, z)))
    return (shape & slab).faces().filter_by(Plane.XY).filter_by_position(Axis.Z, z - 0.011, z - 0.009)


def plate_section(shape, z):
    """The plate's section at z (the largest face; engraved letter islands are separate small faces)."""
    return max(slice_faces(shape, z), key=lambda f: f.area)


def void_map(face):
    """{(centre x, centre y): diameter} for every hole in a section face."""
    out = {}
    for w in face.inner_wires():
        bb = w.bounding_box()
        out[(round(bb.center().X, 2), round(bb.center().Y, 2))] = round(bb.size.X, 3)
    return out


def expected_ladders(module, gauges):
    return {(x, y): round(g + off, 3)
            for y, g in zip(module.LADDER_ROWS_Y, gauges) for x, off in zip(module.LADDER_X, module.GAUGE_OFFSETS)}


def expected_sockets(module):
    return {(module.SOCKET_X[i % 4], module.SOCKET_ROWS_Y[i // 4]): round(pp.hole(module.PEG_D + c), 3)
            for i, c in enumerate(module.SOCKET_CLEARANCES)}


def test_two_valid_parts(built):
    _, parts = built
    assert set(parts) == {"coupon", "peg"}
    for shape in parts.values():
        assert shape.is_valid and shape.volume > 0


def test_plate_size_on_bed(built):
    _, parts = built
    bb = parts["coupon"].bounding_box()
    assert (round(bb.size.X, 3), round(bb.size.Y, 3)) == (160.0, 70.0)
    assert round(bb.min.Z, 3) == 0.0
    assert round(bb.max.Z, 3) == 15.0  # 5 mm plate + 10 mm fins


def test_constants_match_spec(built):
    module, _ = built
    assert module.SOCKET_CLEARANCES == (-0.4, -0.3, -0.2, -0.15, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.3, 0.5)
    assert module.GAUGE_OFFSETS == (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    assert module.PARAMETERS["gauges"] == "3,4,5"
    assert list(module.SOCKET_X) == sorted(module.SOCKET_X)
    assert list(module.LADDER_X) == sorted(module.LADDER_X)
    assert list(module.SOCKET_ROWS_Y) == sorted(module.SOCKET_ROWS_Y, reverse=True)  # row 1 nearest the far edge
    assert list(module.LADDER_ROWS_Y) == sorted(module.LADDER_ROWS_Y, reverse=True)


def test_section_has_ladders_and_sockets(built):
    module, parts = built
    voids = void_map(plate_section(parts["coupon"], 3.0))
    assert voids == {**expected_ladders(module, [3.0, 4.0, 5.0]), **expected_sockets(module)}


def test_sockets_are_blind_with_one_mm_floor(built):
    module, parts = built
    ladders = expected_ladders(module, [3.0, 4.0, 5.0])
    assert void_map(plate_section(parts["coupon"], 0.95)) == ladders  # under the floor: ladder holes only
    assert len(plate_section(parts["coupon"], 1.05).inner_wires()) == len(ladders) + 12


def test_ladder_holes_have_bottom_chamfer(built):
    module, parts = built
    # slab bottom at z = 0.24: a 0.5 mm 45-degree chamfer adds 2 * (0.5 - 0.24) = 0.52 to the diameter
    voids = void_map(plate_section(parts["coupon"], 0.25))
    for centre, d in expected_ladders(module, [3.0, 4.0, 5.0]).items():
        assert voids[centre] == pytest.approx(d + 0.52, abs=0.01)


def test_ladders_stay_nominal_under_non_base_compensation():
    pp.activate(load_profile(overrides={"holes.compensation": 0.13}))
    try:
        module = load_model(COUPON)
        voids = void_map(plate_section(module.build(dict(module.PARAMETERS))["coupon"], 3.0))
        assert {k: voids[k] for k in expected_ladders(module, [3.0, 4.0, 5.0])} == expected_ladders(module, [3.0, 4.0, 5.0])
        for i, c in enumerate(module.SOCKET_CLEARANCES):
            centre = (module.SOCKET_X[i % 4], module.SOCKET_ROWS_Y[i // 4])
            assert voids[centre] == round(module.PEG_D + c + 0.13, 3)
    finally:
        pp.activate(None)


@pytest.mark.parametrize("gauges,values", [
    ("3,4,5", [3.0, 4.0, 5.0]),
    ("3.175,3.969,5.159", [3.175, 3.969, 5.159]),
    ("8,8,8,8", [8.0, 8.0, 8.0, 8.0]),
    ("2", [2.0]),
])
def test_ladders_fit_without_overlaps(built, gauges, values):
    module, _ = built
    coupon = build_with(module, gauges=gauges)["coupon"]
    assert coupon.is_valid
    expected = {**expected_ladders(module, values), **expected_sockets(module)}
    assert void_map(plate_section(coupon, 3.0)) == expected  # distinct voids, exact sizes: nothing merged
    engraved = void_map(plate_section(coupon, 4.8))  # inside the 0.4 mm label layer
    assert {k: engraved.get(k) for k in expected} == expected  # no label touches a hole or socket
    bb = coupon.bounding_box()
    for (x, y), d in expected.items():
        assert min(x - d / 2 - bb.min.X, bb.max.X - x - d / 2, y - d / 2 - bb.min.Y, bb.max.Y - y - d / 2) >= 1.0


@pytest.mark.parametrize("text,needle", [
    ("abc", "not a number"),
    ("", "1 to 4"),
    ("3,4,5,6,7", "1 to 4"),
    ("1.5", "outside 2-8 mm"),
    ("9", "outside 2-8 mm"),
    ("nan", "outside 2-8 mm"),
])
def test_invalid_gauges_rejected(built, text, needle):
    module, _ = built
    with pytest.raises(ValueError, match=needle):
        build_with(module, gauges=text)


def test_gauge_text_tolerates_spaces_and_trailing_comma(built):
    module, _ = built
    assert module.parse_gauges(" 3, 4 ,5,") == [3.0, 4.0, 5.0]


def test_notch_at_far_left_corner(built):
    from build123d import Vector

    _, parts = built
    coupon = parts["coupon"]
    assert not coupon.is_inside(Vector(-79, 34, 2.5))  # notched: far-left (-X, +Y)
    assert not coupon.is_inside(Vector(-78, 33, 2.5))
    for x, y in ((79, 34), (-79, -34), (79, -34), (-75, 30)):
        assert coupon.is_inside(Vector(x, y, 2.5)), (x, y)


def test_fins_are_thin_rectangles_ten_long(built):
    module, parts = built
    fins = slice_faces(parts["coupon"], 10.0)
    sizes = sorted((round(f.bounding_box().size.X, 2), round(f.bounding_box().size.Y, 2)) for f in fins)
    assert sizes == [(t, 10.0) for t in module.FIN_THICKNESSES]
    for f in fins:  # rectangles, not just bounding boxes
        bb = f.bounding_box()
        assert abs(f.area - bb.size.X * bb.size.Y) < 0.01
        assert round(bb.center().Y, 2) == module.FIN_Y
    assert len(slice_faces(parts["coupon"], 6.0)) == 4
    assert len(slice_faces(parts["coupon"], 14.9)) == 4


def test_labels_are_engraved(built):
    module, parts = built
    bare = build_with(module, label_depth=0.001)["coupon"]
    assert bare.volume - parts["coupon"].volume > 1.0
    # engraving stays in the top 0.4 mm: the section just below it is unaffected
    assert len(slice_faces(parts["coupon"], 4.5)) == 1
    assert slice_faces(parts["coupon"], 4.5)[0].area == pytest.approx(slice_faces(bare, 4.5)[0].area, abs=0.01)


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


def calibrate(tmp_path, *args):
    config = tmp_path / "config.toml"
    config.write_text(f'parts_dir = "{(tmp_path / "parts").as_posix()}"\n', encoding="utf-8")
    env = {**os.environ, "PRINT3D_CONFIG": str(config)}
    proc = subprocess.run([sys.executable, str(SCRIPTS / "print3d.py"), "calibrate", *args],
                          capture_output=True, text=True, env=env)
    return proc.returncode, json.loads(proc.stdout)


def test_calibrate_command(tmp_path):
    code, data = calibrate(tmp_path)
    assert code == 0, data
    out = tmp_path / "parts" / "calibration" / "centauri-carbon-pla-plus" / "out"
    assert (out / "calibration_coupon.3mf").is_file()
    with zipfile.ZipFile(out / "calibration_coupon.3mf") as z:
        model_xml = z.read("3D/3dmodel.model").decode("utf-8")
    assert 'name="coupon"' in model_xml and 'name="peg"' in model_xml
    assert data["parameters"]["gauges"] == "3,4,5"
    assert isinstance(data["instructions"], list) and len(data["instructions"]) >= 6
    assert data["profile"]["name"] == "centauri-carbon-pla-plus"
    assert data["profile"]["used"]["holes.compensation"] == 0.2


@pytest.mark.parametrize("value", ["3.175,3.969,5.159", "4"])
def test_calibrate_set_gauges_stays_text(tmp_path, value):
    code, data = calibrate(tmp_path, "--set", f"gauges={value}")
    assert code == 0, data
    assert data["parameters"]["gauges"] == value  # not a tuple or an int


def test_calibrate_unknown_parameter_is_params_error(tmp_path):
    code, data = calibrate(tmp_path, "--set", "nope=1")
    assert code != 0 and data["ok"] is False
    assert data["error"]["stage"] == "params"


def test_calibrate_bad_gauge_names_problem(tmp_path):
    code, data = calibrate(tmp_path, "--set", "gauges=9")
    assert code != 0 and data["ok"] is False
    assert data["error"]["stage"] == "build"
    assert "outside 2-8 mm" in data["error"]["message"]


def _instructions():
    import print3d

    return print3d.CALIBRATION_INSTRUCTIONS


def test_instructions_are_single_print():
    text = " ".join(_instructions()).lower()
    assert "one print" in text
    assert "reprint" not in text and "pass 2" not in text
    for needle in ("0.1 * k - 0.05", "fit = c + used - new", "press <= snug <= sliding <= loose",
                   "calipers", "--set gauges=", 'result.profile.used["holes.compensation"]', "peg"):
        assert needle in text, needle


def test_instructions_describe_layout_by_position(built):
    module, _ = built
    text = " ".join(_instructions()).lower()
    assert "notched corner at the far left" in text and "position is authoritative" in text
    for i in (0, 4, 8):
        row = ", ".join(f"{c:g}" for c in module.SOCKET_CLEARANCES[i:i + 4])
        assert row in text, row
    assert "k = 0..6" in text  # ladder holes, left to right
    assert "0.4, 0.6, 0.8, 1.2" in text
