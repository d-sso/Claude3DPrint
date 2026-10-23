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


PART = (
    "from build123d import Box\n"
    "from print3d_shared import shared\n"
    "\n"
    "PARAMETERS = {**shared('wall'), 'h': 5.0}\n"
    "\n"
    "\n"
    "def build(p):\n"
    "    return Box(10, 10, p['wall'] + p['h'])\n"
)
ASSEMBLY = (
    "from print3d_assembly import Assembly, Layout, Part, Unit, place\n"
    "\n"
    "PARAMETERS = {}\n"
    "\n"
    "\n"
    "def build_assembly(a):\n"
    "    unit = Unit('u', [Part('p', 'parts/p.py', params=PART_PARAMS)])\n"
    "    layout = Layout('l', [('i1', 'u', place()), ('i2', 'u', place(x=20))], overrides=LAYOUT_OVERRIDES)\n"
    "    return Assembly([unit], [layout])\n"
)


def _project(tmp_path, part=PART, part_params="{}", layout_overrides="{}"):
    root = tmp_path / "proj"
    (root / "parts").mkdir(parents=True)
    (root / "interfaces.py").write_text("INTERFACES = {'wall': 3.5}\n", encoding="utf-8")
    (root / "parts" / "p.py").write_text(part, encoding="utf-8")
    text = ASSEMBLY.replace("PARAMETERS = {}\n", f"PARAMETERS = {{}}\nPART_PARAMS = {part_params}\n"
                            f"LAYOUT_OVERRIDES = {layout_overrides}\n")
    (root / "assembly.py").write_text(text, encoding="utf-8")
    return root


def test_run_set_reports_the_value_used(tmp_path):
    root = _project(tmp_path)
    result, _ = run_model.run(root / "parts" / "p.py", set_pairs=["wall=4.0"], export=False)
    assert result["ok"], result["error"]
    assert result["shared"] == {"wall": 4.0}
    result, _ = run_model.run(root / "parts" / "p.py", export=False)
    assert result["shared"] == {"wall": 3.5}


def test_assemble_set_and_part_params_report_the_value_used(tmp_path):
    import assemble_model

    root = _project(tmp_path, part_params="{'wall': 2.0}")
    for pairs, wanted in (([], 2.0), (["wall=4.0"], 2.0)):  # a Part param beats the shared value
        module, params, shared_overrides = assemble_model.load_assembly(root / "assembly.py", pairs)
        assembly = assemble_model.declare_assembly(module, params, shared_overrides)
        items = assemble_model.build_items(assembly, root, shared_overrides, {"warnings": []})
        assert items["u"]["p"].shared == {"wall": wanted}
    root = _project(tmp_path / "b")
    module, params, shared_overrides = assemble_model.load_assembly(root / "assembly.py", ["wall=4.0"])
    assembly = assemble_model.declare_assembly(module, params, shared_overrides)
    items = assemble_model.build_items(assembly, root, shared_overrides, {"warnings": []})
    assert items["u"]["p"].shared == {"wall": 4.0}


def test_layout_override_reports_the_value_used(tmp_path):
    import assemble_model

    root = _project(tmp_path, layout_overrides="{'i2': {'p': {'wall': 5.0}}}")
    module, params, shared_overrides = assemble_model.load_assembly(root / "assembly.py", [])
    assembly = assemble_model.declare_assembly(module, params, shared_overrides)
    result = {"warnings": []}
    builder = assemble_model.Builder(root, shared_overrides, result)
    unit_items = assemble_model.build_items(assembly, root, shared_overrides, result, builder=builder)
    placed = assemble_model.place_layout(assembly.layouts[0], unit_items, builder)
    assert placed["i1.p"].shared == {"wall": 3.5} and placed["i2.p"].shared == {"wall": 5.0}


WARN_PART = PART.replace("{**shared('wall'), 'h': 5.0}", "{'wall': 3.5, 'h': 5.0}")


def test_run_warns_when_a_shared_name_does_not_come_from_shared(tmp_path):
    root = _project(tmp_path, part=WARN_PART)
    result, _ = run_model.run(root / "parts" / "p.py", export=False)
    assert result["ok"], result["error"]
    assert result["warnings"] == [
        "Part 'p' (p.py): parameter 'wall' has a shared name but does not come from shared(); "
        "assemble --set won't change it"
    ]
    assert run_model.run(_project(tmp_path / "ok") / "parts" / "p.py", export=False)[0]["warnings"] == []
