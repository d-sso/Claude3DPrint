import shutil

import pytest
from conftest import FIXTURES

import print3d_profile as pp
from print3d_profile import load_profile
from run_model import run


@pytest.fixture
def model(tmp_path):
    def _copy(name):
        dest = tmp_path / "models" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / name, dest)
        return dest

    yield _copy
    pp.activate(None)


def test_result_records_profile_and_used_values(model):
    prof = load_profile(overrides={"holes.compensation": 0.15})
    result, code = run(model("uses_profile.py"), profile=prof)
    assert code == 0, result["error"]
    block = result["profile"]
    assert block["name"] == "centauri-carbon-pla-plus"
    assert block["file"].endswith("profiles/centauri-carbon-pla-plus.toml")
    assert block["overrides"] == {"holes.compensation": 0.15}
    assert block["used"] == {"fits.sliding": 0.3, "holes.compensation": 0.15}
    assert result["parameters"]["clearance"] == 0.3


def test_default_profile_when_none_given(model):
    result, code = run(model("good_box.py"))
    assert code == 0
    assert result["profile"]["name"] == "centauri-carbon-pla-plus"
    assert result["profile"]["used"] == {} and result["profile"]["overrides"] == {}


def test_second_run_does_not_leak_used_values(model):
    run(model("uses_profile.py"), profile=load_profile())
    result, _ = run(model("good_box.py"), profile=load_profile())
    assert result["profile"]["used"] == {}


def test_usage_error_before_load_has_null_profile(tmp_path):
    result, code = run(tmp_path / "missing.py")
    assert code == 2 and result["profile"] is None


def test_helper_error_inside_build_points_at_model_line(tmp_path):
    path = tmp_path / "bad_fit.py"
    path.write_text(
        "from build123d import *\n"
        "from print3d_profile import fit\n"
        "PARAMETERS = {}\n"
        "def build(p):\n"
        "    gap = fit('tight')\n"
        "    return None\n",
        encoding="utf-8",
    )
    result, code = run(path)
    assert code == 1
    assert result["error"]["stage"] == "build"
    assert result["error"]["line"] == 5
    assert "press" in result["error"]["message"]
    assert result["profile"]["name"] == "centauri-carbon-pla-plus"


def test_profile_block_in_saved_result_json(model):
    import json

    path = model("uses_profile.py")
    result, _ = run(path, profile=load_profile())
    saved = json.loads((path.parent / "out" / "uses_profile.result.json").read_text(encoding="utf-8"))
    assert saved["profile"] == result["profile"]
