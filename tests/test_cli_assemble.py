"""print3d assemble: the command, its result and exit codes, on the boxlid and row fixtures."""
import json
import shutil
from pathlib import Path

import pytest
from conftest import FIXTURES

import print3d

BOXLID = FIXTURES / "assembly" / "boxlid"
ROW = FIXTURES / "assembly" / "row"
KEYS = {"ok", "model", "parameters", "overrides", "shared", "profile", "status", "summary", "units", "layouts",
        "warnings", "timings_s", "error"}


@pytest.fixture(autouse=True)
def no_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINT3D_CONFIG", str(tmp_path / "no-config.toml"))


def assemble(capsys, *args):
    code = print3d.main(["assemble", *map(str, args)])
    return code, json.loads(capsys.readouterr().out)


def copy_project(src, tmp_path):
    dest = tmp_path / src.name
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "out"))
    return dest


def test_assemble_boxlid_cli(capsys, tmp_path):
    out = tmp_path / "out"
    code, data = assemble(capsys, BOXLID / "assembly.py", "--out", out)
    assert code == 0, data["error"]
    assert set(data) == KEYS
    assert data["ok"] is True and data["status"] == "pass" and data["error"] is None
    assert data["summary"] == {"interfaces": {"pass": 8, "fail": 0, "skipped": 0}, "overlaps": 0,
                               "targets": {"pass": 2, "fail": 0}}
    unit = data["units"][0]
    assert unit["name"] == "box" and [p["name"] for p in unit["parts"]] == ["base", "lid"]
    assert all(Path(p["model"]).is_file() for p in unit["parts"])
    for result in unit["interfaces"]:
        assert result["evidence"], result["name"]
        assert all(Path(p).is_file() for p in result["evidence"])
        assert Path(result["evidence"][0]).parent == (out / "assembly" / "box").resolve()
    assert Path(unit["sheets"]["overview"]).is_file()
    assert data["layouts"] == [] and data["parameters"] == {"screw_len": 12.0} and data["overrides"] == {}
    assert data["shared"]["wall"] == 2.0 and data["profile"]["name"]
    saved = json.loads((out / "assembly.result.json").read_text(encoding="utf-8"))
    assert saved["status"] == "pass" and saved["units"][0]["sheets"] == unit["sheets"]


def test_assemble_strict_fail_exit_3(capsys, tmp_path):
    args = [BOXLID / "assembly.py", "--set", "screw_len=6", "--out", tmp_path, "--no-sheets"]
    code, data = assemble(capsys, *args, "--strict")
    assert code == 3 and data["ok"] is True and data["status"] == "fail"
    assert data["overrides"] == {"screw_len": 6.0}
    assert data["summary"]["interfaces"]["fail"] == 2
    bolts = [r for r in data["units"][0]["interfaces"] if r["type"] == "bolted"]
    assert all(r["status"] == "fail" and r["evidence"] == [] for r in bolts)
    assert data["units"][0]["sheets"] == {}
    code, data = assemble(capsys, *args)
    assert code == 0 and data["status"] == "fail"


def test_assemble_usage_errors(capsys, tmp_path):
    code, data = assemble(capsys, tmp_path / "missing.py")
    assert code == 2 and data["ok"] is False and data["error"]["stage"] == "usage"
    code, data = assemble(capsys, BOXLID / "assembly.py", "--level", "bogus")
    assert code == 2 and data["error"]["stage"] == "usage"
    code, data = assemble(capsys, BOXLID / "assembly.py", "--unit", "nope", "--out", tmp_path)
    assert code == 2 and "box" in data["error"]["message"]
    code, data = assemble(capsys, BOXLID / "assembly.py", "--unit", "box", "--level", "layout")
    assert code == 2
    code, data = assemble(capsys, BOXLID / "assembly.py", "--set", "nope=1", "--out", tmp_path)
    assert code == 1 and data["error"]["stage"] == "params"


def test_assemble_notes_become_warnings(capsys, tmp_path):
    project = copy_project(BOXLID, tmp_path)
    path = project / "assembly.py"
    text = path.read_text(encoding="utf-8")
    old = 'return Assembly([Unit("box", items, interfaces, targets)])'
    assert old in text
    note = "fixture.md says the wall is 2.5 mm, interfaces.py has 2.0; the checks use 2.0"
    path.write_text(text.replace(old, old[:-1] + f", notes=[{note!r}])"), encoding="utf-8")
    code, data = assemble(capsys, path, "--no-sheets")
    assert code == 0, data["error"]
    assert note in data["warnings"]
    assert (project / "out" / "assembly.result.json").is_file()  # out/ next to assembly.py by default


def test_assemble_part_error_names_the_part(capsys, tmp_path):
    project = copy_project(BOXLID, tmp_path)
    lid = project / "parts" / "lid" / "lid.py"
    lid.write_text(lid.read_text(encoding="utf-8").replace("def build(p):", "def build(p):\n    1 / 0"),
                   encoding="utf-8")
    code, data = assemble(capsys, project / "assembly.py", "--no-sheets")
    assert code == 1 and data["ok"] is False and data["status"] is None
    assert data["error"]["stage"] == "build" and "Part 'lid'" in data["error"]["message"]
    assert any("lid.py" in line for line in data["error"]["traceback"])


def test_assemble_level_filter(capsys, tmp_path):
    code, data = assemble(capsys, ROW / "assembly.py", "--level", "unit", "--out", tmp_path)
    assert code == 0, data["error"]
    assert data["layouts"] == [] and [u["name"] for u in data["units"]] == ["cell"]
    assert Path(data["units"][0]["sheets"]["overview"]).is_file()


def test_assemble_row_layout(capsys, tmp_path):
    code, data = assemble(capsys, ROW / "assembly.py", "--layout", "row", "--out", tmp_path)
    assert code == 0, data["error"]
    assert data["units"] == [] and data["status"] == "pass"
    layout, = data["layouts"]
    assert layout["name"] == "row" and layout["exact"] == ["c1", "c2"]
    strip, = layout["interfaces"]
    assert strip["status"] == "pass"
    assert Path(strip["evidence"][0]).parent == (tmp_path / "assembly" / "row").resolve()
    assert Path(layout["sheets"]["overview"]).is_file()
    assert data["summary"]["targets"] == {"pass": 2, "fail": 0}
