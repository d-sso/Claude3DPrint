import json
import shutil
from pathlib import Path

import pytest
import trimesh
from conftest import FIXTURES

import run_model
from run_model import run


@pytest.fixture
def model(tmp_path):
    def _copy(fixture_name, dest_name=None):
        dest_dir = tmp_path / "models"
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / (dest_name or fixture_name)
        shutil.copyfile(FIXTURES / fixture_name, dest)
        return dest

    return _copy


def test_good_model_exports_and_reports(model):
    path = model("good_box.py")
    result, code = run(path)
    assert code == 0, result["error"]
    assert result["ok"] is True and result["error"] is None
    assert result["model"] == path.resolve().as_posix()
    assert result["parameters"]["length"] == 40.0 and result["overrides"] == {}
    (part,) = result["parts"]
    assert part["name"] == "main"
    stl = Path(part["files"]["stl"])
    assert stl.name == "good_box.stl" and stl.parent == path.parent / "out"
    mesh = trimesh.load(stl)
    assert mesh.is_watertight
    assert mesh.volume == pytest.approx(18000, rel=1e-3)
    assert list(mesh.extents) == pytest.approx([40, 30, 15], abs=1e-3)
    assert Path(result["files"]["3mf"]).name == "good_box.3mf"
    assert set(result["timings_s"]) == {"load", "build", "export"}
    saved = json.loads((path.parent / "out" / "good_box.result.json").read_text())
    assert saved == result


def test_multi_part_names_files_and_single_3mf(model):
    path = model("multi_part.py")
    result, code = run(path)
    assert code == 0, result["error"]
    names = [Path(p["files"]["stl"]).name for p in result["parts"]]
    assert names == ["multi_part-base.stl", "multi_part-lid.stl"]
    scene = trimesh.load(result["files"]["3mf"])
    assert len(scene.geometry) == 2


def test_set_override_changes_size(model):
    result, code = run(model("good_box.py"), ["length=60"])
    assert code == 0
    assert result["overrides"] == {"length": 60.0}
    assert result["parts"][0]["bbox_mm"]["size"][0] == pytest.approx(60, abs=1e-3)


def test_unknown_override_is_params_error(model):
    result, code = run(model("good_box.py"), ["lenght=60"])
    assert code == 1
    assert result["error"]["stage"] == "params"
    assert "length (float" in result["error"]["message"]


def test_step_format_and_custom_out_dir(model, tmp_path):
    out = tmp_path / "custom out"
    result, code = run(model("good_box.py"), formats=["step"], out_dir=out)
    assert code == 0
    step = Path(result["parts"][0]["files"]["step"])
    assert step.parent == out and step.is_file()
    assert "stl" not in result["parts"][0]["files"] and result["files"] == {}


def test_syntax_error_reports_load_stage_and_line(model):
    result, code = run(model("broken_syntax.py"))
    assert code == 1
    assert result["error"]["stage"] == "load"
    assert result["error"]["line"] == 4


def test_build_error_points_at_model_line(model):
    result, code = run(model("broken_build.py"))
    assert code == 1
    error = result["error"]
    assert error["stage"] == "build"
    assert error["line"] == 10
    assert error["traceback"] and all("broken_build.py" in frame for frame in error["traceback"])
    assert any(":10 in build" in frame for frame in error["traceback"])


def test_bad_return_is_build_error(model):
    result, code = run(model("bad_return.py"))
    assert code == 1
    assert result["error"]["stage"] == "build"
    assert "int" in result["error"]["message"]


def test_missing_model_is_usage_error_without_result_file(tmp_path):
    result, code = run(tmp_path / "nope.py")
    assert code == 2
    assert result["error"]["stage"] == "usage"
    assert not (tmp_path / "out").exists()


def test_unknown_format_is_usage_error(model):
    result, code = run(model("good_box.py"), formats=["obj"])
    assert code == 2
    assert "obj" in result["error"]["message"]


def test_locked_file_permission_error_is_export_error(model, monkeypatch):
    def locked(shape, path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(run_model, "_write_stl", locked)
    result, code = run(model("good_box.py"))
    assert code == 1
    assert result["error"]["stage"] == "export"
    assert "good_box.stl" in result["error"]["message"]
    assert "slicer" in result["error"]["message"]
    assert result["parts"][0]["name"] == "main"


def test_writer_returning_false_is_export_error(model, monkeypatch):
    monkeypatch.setattr(run_model, "_write_stl", lambda shape, path: False)
    result, code = run(model("good_box.py"))
    assert code == 1
    assert result["error"]["stage"] == "export"
    assert "slicer" in result["error"]["message"]


def test_unexpected_internal_error_keeps_full_traceback(model, monkeypatch):
    def boom(name, shape):
        raise KeyError("internal")

    monkeypatch.setattr(run_model, "summarize", boom)
    result, code = run(model("good_box.py"))
    assert code == 1
    assert result["error"]["stage"] == "build"
    assert result["error"]["type"] == "KeyError"
    assert any("run_model.py" in line for line in result["error"]["traceback"])
