import pytest
from conftest import FIXTURES

from run_model import RunError, load_model, normalize_parts, summarize


def test_load_good_model():
    module = load_model(FIXTURES / "good_box.py")
    assert module.PARAMETERS["length"] == 40.0
    assert callable(module.build)


def test_load_syntax_error_reports_line():
    with pytest.raises(RunError) as err:
        load_model(FIXTURES / "broken_syntax.py")
    assert err.value.stage == "load"
    assert err.value.line == 4


def test_load_missing_build(tmp_path):
    path = tmp_path / "nobuild.py"
    path.write_text("PARAMETERS = {}\n")
    with pytest.raises(RunError) as err:
        load_model(path)
    assert err.value.stage == "load"
    assert "build" in err.value.message


@pytest.mark.parametrize(
    "source",
    [
        "def build(p):\n    pass\n",
        "PARAMETERS = [1]\ndef build(p):\n    pass\n",
        "PARAMETERS = {'a': [1]}\ndef build(p):\n    pass\n",
    ],
)
def test_load_rejects_bad_parameters(tmp_path, source):
    path = tmp_path / "bad.py"
    path.write_text(source)
    with pytest.raises(RunError) as err:
        load_model(path)
    assert err.value.stage == "load"
    assert "PARAMETERS" in err.value.message


def test_load_import_error_is_load_stage(tmp_path):
    path = tmp_path / "badimport.py"
    path.write_text("import not_a_real_module\nPARAMETERS = {}\ndef build(p):\n    pass\n")
    with pytest.raises(RunError) as err:
        load_model(path)
    assert err.value.stage == "load"
    assert "not_a_real_module" in err.value.message


def test_load_hyphenated_filename(tmp_path):
    path = tmp_path / "phone-stand.py"
    path.write_text((FIXTURES / "good_box.py").read_text())
    assert load_model(path).PARAMETERS["length"] == 40.0


def test_normalize_single_shape_is_main():
    module = load_model(FIXTURES / "good_box.py")
    parts = normalize_parts(module.build(dict(module.PARAMETERS)))
    assert list(parts) == ["main"]


def test_normalize_dict_keeps_names():
    module = load_model(FIXTURES / "multi_part.py")
    parts = normalize_parts(module.build(dict(module.PARAMETERS)))
    assert list(parts) == ["base", "lid"]


@pytest.mark.parametrize("value,needle", [(42, "int"), ({}, "empty"), ({"Bad Name": None}, "Bad Name")])
def test_normalize_rejects(value, needle):
    with pytest.raises(RunError) as err:
        normalize_parts(value)
    assert err.value.stage == "build"
    assert needle in err.value.message


def test_normalize_builder_hints_part_dot_part():
    from build123d import Box, BuildPart

    with BuildPart() as part:
        Box(1, 1, 1)
    with pytest.raises(RunError) as err:
        normalize_parts(part)
    assert "part.part" in err.value.message


def test_summarize_box():
    module = load_model(FIXTURES / "good_box.py")
    summary = summarize("main", module.build(dict(module.PARAMETERS)))
    assert summary["name"] == "main"
    assert summary["volume_mm3"] == pytest.approx(18000, rel=1e-3)
    assert summary["bbox_mm"]["size"] == pytest.approx([40, 30, 15], abs=1e-3)
    assert summary["bbox_mm"]["min"][2] == pytest.approx(0, abs=1e-3)
    assert summary["valid"] is True
    assert summary["files"] == {}


def test_summarize_rejects_zero_volume():
    class EmptyShape:
        volume = 0.0

    with pytest.raises(RunError) as err:
        summarize("main", EmptyShape())
    assert err.value.stage == "build"
    assert "volume" in err.value.message
