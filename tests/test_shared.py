"""Project roots and shared interface values for part models."""
import shutil
import sys

import pytest

import print3d_shared
import run_model
from conftest import FIXTURES

BOXLID = FIXTURES / "assembly" / "boxlid"
LID = BOXLID / "parts" / "lid" / "lid.py"


def test_find_project_root_nearest_assembly(tmp_path):
    assert print3d_shared.find_project_root(LID) == BOXLID
    assert print3d_shared.find_project_root(tmp_path / "x.py") is None


def test_shared_returns_values():
    with print3d_shared.activate(BOXLID):
        assert print3d_shared.shared("wall", "bolt_dx") == {"wall": 2.0, "bolt_dx": 14.0}


def test_shared_override_applies():
    with print3d_shared.activate(BOXLID, {"wall": 2.4}):
        assert print3d_shared.shared("wall") == {"wall": 2.4}


def test_shared_unknown_name_lists_valid():
    with print3d_shared.activate(BOXLID):
        with pytest.raises(KeyError) as info:
            print3d_shared.shared("nope")
    assert "nope" in str(info.value)
    assert "box_x" in str(info.value)


def test_shared_outside_project_errors():
    with pytest.raises(RuntimeError, match="no assembly.py"):
        print3d_shared.shared("wall")


def test_run_part_in_project_uses_shared():
    result, _ = run_model.run(LID, export=False)
    assert result["ok"], result["error"]
    assert result["shared"]["bolt_dx"] == 14.0


def test_run_outside_project_has_empty_shared():
    result, _ = run_model.run(FIXTURES / "good_box.py", export=False)
    assert result["ok"], result["error"]
    assert result["shared"] == {}


def test_interfaces_syntax_error_names_file(tmp_path):
    root = tmp_path / "boxlid"
    shutil.copytree(BOXLID, root)
    (root / "interfaces.py").write_text("INTERFACES = {\n    'wall': 2.0,\n    'x' 3\n}\n", encoding="utf-8")
    result, _ = run_model.run(root / "parts" / "lid" / "lid.py", export=False)
    assert not result["ok"]
    assert result["error"]["stage"] == "load"
    assert "interfaces.py" in result["error"]["message"]
    assert ":3" in result["error"]["message"]


def test_sys_path_restored_after_load():
    before = list(sys.path)
    run_model.load_model(LID)
    assert sys.path == before


def test_interfaces_runtime_error_names_line(tmp_path):
    root = tmp_path / "boxlid"
    shutil.copytree(BOXLID, root)
    (root / "interfaces.py").write_text("A = 1\nB = 2\nINTERFACES = {'wall': undefined_name}\n", encoding="utf-8")
    result, _ = run_model.run(root / "parts" / "lid" / "lid.py", export=False)
    assert not result["ok"]
    assert result["error"]["stage"] == "load"
    assert "interfaces.py:3" in result["error"]["message"]


def test_sys_path_change_outside_project_kept(tmp_path):
    model = tmp_path / "m.py"
    model.write_text("import sys\nsys.path.append('print3d-marker')\nPARAMETERS = {}\ndef build(p):\n    pass\n",
                     encoding="utf-8")
    try:
        run_model.load_model(model)
        assert "print3d-marker" in sys.path
    finally:
        while "print3d-marker" in sys.path:
            sys.path.remove("print3d-marker")
