import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES, SCRIPTS

import print3d


@pytest.fixture
def cli(tmp_path):
    parts = tmp_path / "parts"
    config = tmp_path / "config.toml"
    config.write_text(f'parts_dir = "{parts.as_posix()}"\n', encoding="utf-8")
    env = {**os.environ, "PRINT3D_CONFIG": str(config)}

    def _run(*args):
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "print3d.py"), *map(str, args)],
            capture_output=True,
            text=True,
            env=env,
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"stdout is not a single JSON document:\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
        return proc.returncode, data

    _run.parts_dir = parts
    return _run


def copy_fixture(tmp_path, name, folder="models"):
    dest = tmp_path / folder / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / name, dest)
    return dest


def test_run_good_model(cli, tmp_path):
    code, data = cli("run", copy_fixture(tmp_path, "good_box.py"))
    assert code == 0 and data["ok"] is True


def test_run_repeated_set(cli, tmp_path):
    code, data = cli("run", copy_fixture(tmp_path, "good_box.py"), "--set", "length=60", "--set", "label=lid")
    assert code == 0
    assert data["overrides"] == {"length": 60.0, "label": "lid"}


def test_run_formats_option(cli, tmp_path):
    code, data = cli("run", copy_fixture(tmp_path, "good_box.py"), "--formats", "stl,step")
    assert code == 0
    assert set(data["parts"][0]["files"]) == {"stl", "step"} and data["files"] == {}


def test_model_prints_do_not_corrupt_stdout(cli, tmp_path):
    code, data = cli("run", copy_fixture(tmp_path, "noisy.py"))
    assert code == 0 and data["ok"] is True


def test_path_with_spaces(cli, tmp_path):
    code, data = cli("run", copy_fixture(tmp_path, "good_box.py", folder="My Parts/box"))
    assert code == 0
    assert "My Parts" in data["parts"][0]["files"]["stl"]


@pytest.mark.parametrize("args", [[], ["run"], ["run", "x.py", "--bogus"], ["fly"]])
def test_usage_errors_are_json_exit_2(cli, args):
    code, data = cli(*args)
    assert code == 2
    assert data["ok"] is False and data["error"]["stage"] == "usage"


def test_missing_model_file(cli, tmp_path):
    code, data = cli("run", tmp_path / "missing.py")
    assert code == 2 and data["error"]["stage"] == "usage"


def test_new_creates_buildable_part_in_parts_dir(cli):
    code, data = cli("new", "phone-stand")
    assert code == 0, data
    model = cli.parts_dir / "phone-stand" / "phone-stand.py"
    assert Path(data["model"]) == model.resolve()
    code, built = cli("run", model)
    assert code == 0, built["error"]
    assert built["parts"][0]["volume_mm3"] == pytest.approx(18000, rel=1e-3)


def test_new_refuses_existing_part(cli):
    assert cli("new", "bracket")[0] == 0
    code, data = cli("new", "bracket")
    assert code == 2 and "already exists" in data["error"]["message"]


@pytest.mark.parametrize("name", ["Bad_Name", "-lead", "has space", "trail-"])
def test_new_rejects_non_kebab_names(cli, name):
    code, data = cli("new", name)
    assert code == 2 and "kebab-case" in data["error"]["message"]


def test_new_with_dir_override(cli, tmp_path):
    code, data = cli("new", "one-off", "--dir", tmp_path / "elsewhere")
    assert code == 0
    assert Path(data["model"]).parent == (tmp_path / "elsewhere" / "one-off").resolve()


def test_parts_dir_defaults_to_home_3dparts():
    assert print3d.resolve_parts_dir({}) == Path.home() / "3DParts"


def test_config_path_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT3D_CONFIG", str(tmp_path / "c.toml"))
    assert print3d.config_path() == tmp_path / "c.toml"


def test_missing_config_file_gives_empty_config(monkeypatch, tmp_path):
    monkeypatch.setenv("PRINT3D_CONFIG", str(tmp_path / "absent.toml"))
    assert print3d.load_config() == {}


def test_malformed_config_error_names_the_file(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("parts_dir = = oops\n", encoding="utf-8")
    env = {**os.environ, "PRINT3D_CONFIG": str(bad)}
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / "print3d.py"), "new", "thing"], capture_output=True, text=True, env=env
    )
    data = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert str(bad) in data["error"]["message"]
