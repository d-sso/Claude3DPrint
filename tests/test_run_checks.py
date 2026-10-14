"""Runner integration: the check stage, model CHECKS/expected(), --strict exit codes and export=False."""
import json
import shutil

import pytest
from conftest import FIXTURES

from run_model import run


@pytest.fixture
def model(tmp_path):
    def _copy(fixture_name):
        dest = tmp_path / "models" / fixture_name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(FIXTURES / fixture_name, dest)
        return dest

    return _copy


def _inline(tmp_path, source, name="inline.py"):
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


BOX = (
    "from build123d import *\n"
    "PARAMETERS = {}\n"
    "def build(p):\n"
    "    with BuildPart() as part:\n"
    "        Box(40, 30, 15, align=(Align.CENTER, Align.CENTER, Align.MIN))\n"
    "    return part.part\n"
)


def check_status(result, part="main"):
    return {r["check"]: r["status"] for r in result["checks"]["parts"][part]["results"]}


def test_good_box_passes_every_check_and_strict_exits_0(model):
    result, code = run(model("good_box.py"), strict=True)
    assert code == 0, result["error"]
    checks = result["checks"]
    assert checks["status"] == "pass"
    assert set(check_status(result).values()) == {"pass"}
    assert checks["thresholds"]["min_wall"] == 0.8 and checks["sources"]["min_wall"] == "profile"
    assert checks["sources"]["samples"] == "default"
    assert set(result["timings_s"]) == {"load", "build", "export", "check"}
    assert result["timings_s"]["check"] < 5


@pytest.mark.parametrize(
    "fixture, check",
    [("thin_wall.py", "wall"), ("thin_fin.py", "thin"), ("oversized.py", "bed_fit"), ("wrong_size.py", "size")],
)
def test_planted_faults_fail_their_check(model, fixture, check):
    result, code = run(model(fixture))
    assert code == 0, result["error"]  # without --strict a failed check is reported, not an error
    assert result["ok"] is True and result["checks"]["status"] == "fail"
    assert check_status(result)[check] == "fail"


@pytest.mark.parametrize("fixture", ["thin_wall.py", "thin_fin.py", "oversized.py", "wrong_size.py"])
def test_strict_exits_3_on_fail_and_still_exports(model, fixture):
    path = model(fixture)
    result, code = run(path, strict=True)
    assert code == 3
    assert result["ok"] is True and result["error"] is None
    assert result["parts"][0]["files"]["stl"] and result["files"]["3mf"]
    saved = json.loads((path.parent / "out" / f"{path.stem}.result.json").read_text())
    assert saved["checks"]["status"] == "fail"


def test_overhang_only_warns_and_strict_exits_0(model):
    result, code = run(model("overhang_t.py"), strict=True)
    assert code == 0
    assert result["checks"]["status"] == "warn"
    assert check_status(result)["overhang"] == "warn"


def test_no_check_leaves_checks_null(model):
    result, code = run(model("thin_wall.py"), check=False, strict=True)
    assert code == 0 and result["checks"] is None
    assert "check" not in result["timings_s"]


def test_check_only_mode_writes_nothing(model):
    path = model("thin_wall.py")
    result, code = run(path, export=False, strict=True)
    assert code == 3 and result["checks"]["status"] == "fail"
    assert result["files"] == {} and result["parts"][0]["files"] == {}
    assert "export" not in result["timings_s"]
    assert not (path.parent / "out").exists()


def test_check_only_mode_still_reports_usage_errors(tmp_path):
    result, code = run(tmp_path / "missing.py", export=False)
    assert code == 2 and result["error"]["stage"] == "usage"


def test_model_checks_dict_overrides_config_and_cli_overrides_model(tmp_path):
    path = _inline(tmp_path, BOX + "CHECKS = {'min_wall': 1.5}  # test\n")
    result, _ = run(path, config_checks={"min_wall": 1.0, "samples": 400})
    assert result["checks"]["thresholds"]["min_wall"] == 1.5
    assert result["checks"]["sources"]["min_wall"] == "model" and result["checks"]["sources"]["samples"] == "config"
    result, _ = run(path, check_pairs=["min_wall=2"])
    assert result["checks"]["thresholds"]["min_wall"] == 2.0 and result["checks"]["sources"]["min_wall"] == "cli"


def test_unknown_check_key_is_params_error(model):
    result, code = run(model("good_box.py"), check_pairs=["min_wal=1"])
    assert code == 1 and result["error"]["stage"] == "params"
    assert "min_wal" in result["error"]["message"] and "min_wall" in result["error"]["message"]


def test_wrong_type_model_check_names_the_layer(tmp_path):
    result, code = run(_inline(tmp_path, BOX + "CHECKS = {'min_wall': '0.6'}  # test\n"))
    assert code == 1 and result["error"]["stage"] == "params"
    assert "'min_wall'" in result["error"]["message"] and "(from the model's CHECKS)" in result["error"]["message"]


def test_malformed_check_pair_is_usage_error(model):
    result, code = run(model("good_box.py"), check_pairs=["min_wall"])
    assert code == 2 and "--check expects key=value" in result["error"]["message"]


def test_checks_not_a_dict_is_load_error(tmp_path):
    result, code = run(_inline(tmp_path, BOX + "CHECKS = 0.6\n"))
    assert code == 1 and result["error"]["stage"] == "load" and "CHECKS" in result["error"]["message"]


def test_expected_error_points_at_model_line(model):
    result, code = run(model("bad_expected.py"))
    assert code == 1
    error = result["error"]
    assert error["stage"] == "check" and error["line"] == 14 and "KeyError" in error["message"]
    assert result["parts"][0]["files"]["stl"]  # exported before the check stage failed


@pytest.mark.parametrize(
    "body, needle",
    [
        ("return (40, 30, 15)", "must return a dict"),
        ("return {'lid': (40, 30, 15)}", "unknown part(s) 'lid'; parts are 'main'"),
        ("return {'main': (40, 30)}", "expected()['main'] must be (x, y, z)"),
        ("return {'main': (40, 30, '15')}", "must be (x, y, z)"),
    ],
)
def test_bad_expected_return_is_check_error(tmp_path, body, needle):
    result, code = run(_inline(tmp_path, BOX + f"def expected(p):\n    {body}\n"))
    assert code == 1 and result["error"]["stage"] == "check" and result["error"]["line"] == 7
    assert needle in result["error"]["message"]


def test_expected_signature_error_points_at_def_line(tmp_path):
    result, code = run(_inline(tmp_path, BOX + "def expected():\n    return {}\n"))
    assert code == 1 and result["error"]["stage"] == "check"
    assert "TypeError" in result["error"]["message"] and result["error"]["line"] == 7


def test_lambda_expected_bad_shape_points_at_its_line(tmp_path):
    result, code = run(_inline(tmp_path, BOX + "expected = lambda p: (40, 30, 15)\n"))
    assert code == 1 and "must return a dict" in result["error"]["message"] and result["error"]["line"] == 7


NP_BOX = BOX + "import numpy as np\n"


def test_expected_accepts_numpy_sizes_and_result_is_json(tmp_path):
    source = NP_BOX + "def expected(p):\n    return {'main': (np.int64(40), np.float64(30), np.float32(15))}\n"
    path = _inline(tmp_path, source)
    result, code = run(path)
    assert code == 0, result["error"]
    assert check_status(result)["size"] == "pass"
    json.dumps(result)  # numpy values must not leak into the report
    saved = json.loads((path.parent / "out" / f"{path.stem}.result.json").read_text())
    assert saved["checks"]["status"] == "pass"


@pytest.mark.parametrize("bad", ["'15'", "True", "np.bool_(True)", "float('nan')", "float('inf')", "np.float64('nan')"])
def test_expected_rejects_non_real_or_non_finite_sizes(tmp_path, bad):
    result, code = run(_inline(tmp_path, NP_BOX + f"def expected(p):\n    return {{'main': (40, 30, {bad})}}\n"))
    assert code == 1 and result["error"]["stage"] == "check"
    assert "must be (x, y, z)" in result["error"]["message"]


def test_expected_not_callable_is_load_error(tmp_path):
    result, code = run(_inline(tmp_path, BOX + "expected = (40, 30, 15)\n"))
    assert code == 1 and result["error"]["stage"] == "load" and "expected" in result["error"]["message"]


def test_multi_part_checks_each_part(model):
    result, code = run(model("multi_part.py"))
    assert code == 0
    parts = result["checks"]["parts"]
    assert set(parts) == {"base", "lid"}
    assert check_status(result, "lid")["overhang"] == "pass"  # a floating lid is not an overhang
    assert check_status(result, "base")["size"] == "skipped"


def test_expected_with_partial_parts_skips_the_rest(tmp_path):
    source = (
        "from build123d import *\nPARAMETERS = {}\n"
        "def build(p):\n"
        "    with BuildPart() as a:\n        Box(10, 10, 10)\n"
        "    with BuildPart() as b:\n        Box(20, 20, 20)\n"
        "    return {'a': a.part, 'b': b.part}\n"
        "def expected(p):\n    return {'a': (10, 10, 10)}\n"
    )
    result, code = run(_inline(tmp_path, source))
    assert code == 0
    assert check_status(result, "a")["size"] == "pass" and check_status(result, "b")["size"] == "skipped"


def test_failed_build_leaves_checks_null(model):
    result, code = run(model("broken_build.py"), strict=True)
    assert code == 1 and result["checks"] is None


def test_profile_supplies_printer_thresholds(model):
    import print3d_profile

    profile = print3d_profile.load_profile("centauri-carbon-pla-plus", {"checks.min_wall": 1.0})
    result, code = run(model("good_box.py"), profile=profile, check_pairs=["overhang_angle=50"])
    assert code == 0, result["error"]
    thresholds, sources = result["checks"]["thresholds"], result["checks"]["sources"]
    assert thresholds["bed_x"] == profile["values"]["printer"]["bed_x"] and sources["bed_x"] == "profile"
    assert thresholds["min_wall"] == 1.0 and sources["min_wall"] == "profile"
    assert sources["overhang_angle"] == "cli" and sources["samples"] == "default"
    assert result["profile"]["name"] == "centauri-carbon-pla-plus"  # #5's record survives the refactor


def test_check_only_mode_records_the_profile(model):
    import print3d_profile

    profile = print3d_profile.load_profile("centauri-carbon-pla-plus", {})
    result, code = run(model("good_box.py"), profile=profile, export=False)
    assert code == 0 and result["profile"]["name"] == "centauri-carbon-pla-plus"


def test_missing_check_dependency_says_to_rerun_setup(model, monkeypatch):
    # A user who pulls this change without re-running setup.ps1 has no rtree in the venv.
    import sys

    monkeypatch.setitem(sys.modules, "rtree", None)
    monkeypatch.setitem(sys.modules, "rtree.index", None)
    result, code = run(model("good_box.py"))
    assert code == 1
    error = result["error"]
    assert error["stage"] == "check" and not error["message"].startswith("Internal")
    assert "rtree" in error["message"] and "setup.ps1" in error["message"]
    assert result["parts"][0]["files"]["stl"]  # the export still happened
