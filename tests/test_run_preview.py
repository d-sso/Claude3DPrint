"""Runner integration: the preview stage, output paths, --no-preview, failure handling and the time budget."""
import shutil
from pathlib import Path

import pytest
from PIL import Image
from conftest import FIXTURES

import render_model
from run_model import run


@pytest.fixture
def model(tmp_path):
    def _copy(fixture_name):
        dest = tmp_path / "models" / fixture_name
        dest.parent.mkdir(exist_ok=True)
        shutil.copyfile(FIXTURES / fixture_name, dest)
        return dest

    return _copy


def test_run_writes_one_sheet_and_records_it(model):
    path = model("good_box.py")
    result, code = run(path)
    assert code == 0, result["error"]
    preview = Path(result["parts"][0]["files"]["preview"])
    assert preview == (path.parent / "out" / "good_box.preview.png").resolve()
    assert Image.open(preview).size == render_model.SHEET_SIZE
    assert "preview" in result["timings_s"] and result["warnings"] == []


def test_multi_part_writes_a_sheet_per_part(model):
    result, code = run(model("multi_part.py"))
    assert code == 0, result["error"]
    names = [Path(p["files"]["preview"]).name for p in result["parts"]]
    assert names == ["multi_part-base.preview.png", "multi_part-lid.preview.png"]
    assert all(Path(p["files"]["preview"]).is_file() for p in result["parts"])


def test_no_preview_and_check_write_no_png(model):
    path = model("good_box.py")
    result, _ = run(path, preview=False)
    assert "preview" not in result["parts"][0]["files"] and "preview" not in result["timings_s"]
    assert not list((path.parent / "out").glob("*.png"))
    result, _ = run(path, export=False)
    assert "preview" not in result["parts"][0]["files"]
    assert not list((path.parent / "out").glob("*.png"))


def test_preview_failure_is_a_warning_not_an_error(model, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(render_model, "preview_sheet", boom)
    result, code = run(model("good_box.py"), strict=True)
    assert code == 0 and result["ok"] is True
    assert Path(result["parts"][0]["files"]["stl"]).is_file()
    assert "preview" not in result["parts"][0]["files"]
    assert result["warnings"] == ["Preview failed for part 'main': RuntimeError: renderer exploded"]


def test_locked_png_is_a_warning(model, monkeypatch):
    def locked(image, path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(render_model, "save_png", locked)
    result, code = run(model("good_box.py"))
    assert code == 0 and "preview" not in result["parts"][0]["files"]
    assert result["warnings"][0].startswith("Preview failed for part 'main': PermissionError:")


def test_missing_render_dependency_is_a_warning(model, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "render_model", None)  # makes `import render_model` raise ImportError
    result, code = run(model("good_box.py"))
    assert code == 0 and result["ok"] is True
    assert "preview" not in result["parts"][0]["files"]
    assert result["warnings"][0].startswith("Preview skipped: the Python package")
    assert "setup.ps1" in result["warnings"][0]


def test_build_error_runs_no_preview(model):
    result, code = run(model("broken_build.py"))
    assert code == 1 and "preview" not in result["timings_s"]


def test_failing_check_regions_become_markers(model, monkeypatch):
    seen = {}
    real = render_model.preview_sheet

    def spy(name, mesh, info, regions=()):
        seen["regions"], seen["info"] = list(regions), info
        return real(name, mesh, info, regions)

    monkeypatch.setattr(render_model, "preview_sheet", spy)
    result, code = run(model("thin_wall.py"))
    assert code == 0, result["error"]
    assert seen["info"]["checks"] == "fail" and seen["info"]["sealed"] is True
    assert seen["regions"] and {r[0] for r in seen["regions"]} >= {"wall"}


def test_no_check_renders_without_markers(model, monkeypatch):
    seen = {}
    real = render_model.preview_sheet

    def spy(name, mesh, info, regions=()):
        seen["regions"], seen["info"] = list(regions), info
        return real(name, mesh, info, regions)

    monkeypatch.setattr(render_model, "preview_sheet", spy)
    result, _ = run(model("thin_wall.py"), check=False)
    assert seen["regions"] == [] and seen["info"]["checks"] is None
    assert "preview" in result["parts"][0]["files"]


def test_hollow_cup_section_shows_walls(model):
    result, code = run(model("hollow_cup.py"))
    assert code == 0, result["error"]
    import numpy as np

    sheet = np.asarray(Image.open(result["parts"][0]["files"]["preview"]).convert("RGB"))
    cut = np.all(sheet == np.array(render_model.CUT_RGB, dtype=np.uint8), axis=-1)
    assert cut[render_model.TITLE_H + render_model.PANEL:].any()


def test_typical_part_previews_within_budget(model):
    result, code = run(model("filleted_bracket.py"))
    assert code == 0, result["error"]
    assert result["timings_s"]["preview"] < 10
