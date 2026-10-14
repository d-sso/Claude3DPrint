import json
import os
import shutil
import subprocess
import sys
import tomllib

import pytest
from conftest import FIXTURES, SCRIPTS


@pytest.fixture
def cli(tmp_path):
    parts = tmp_path / "parts"
    config = tmp_path / "config.toml"
    config.write_bytes(f'parts_dir = "{parts.as_posix()}"  # mine\r\n'.encode("utf-8"))
    env = {**os.environ, "PRINT3D_CONFIG": str(config)}

    def _run(*args):
        proc = subprocess.run([sys.executable, str(SCRIPTS / "print3d.py"), *map(str, args)],
                              capture_output=True, text=True, env=env)
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pytest.fail(f"stdout is not JSON:\n{proc.stdout}\n--- stderr ---\n{proc.stderr}")
        return proc.returncode, data

    _run.config = config
    return _run


def model(tmp_path, name):
    dest = tmp_path / "models" / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / name, dest)
    return dest


def test_profile_show_reports_base_sources(cli):
    code, data = cli("profile", "show")
    assert code == 0 and data["ok"] is True
    assert data["profile"] == "centauri-carbon-pla-plus"
    assert data["values"]["fits.sliding"] == {"value": 0.3, "source": "base"}
    assert data["config"].endswith("config.toml")


def test_profile_set_writes_per_profile_table_and_preserves_rest(cli):
    code, data = cli("profile", "set", "holes.compensation=0.18", "fits.snug=0.15")
    assert code == 0, data
    assert data["values"]["holes.compensation"] == {"value": 0.18, "source": "override"}
    raw = cli.config.read_bytes().decode("utf-8")
    assert raw.startswith('parts_dir = "')
    assert "  # mine\r\n" in raw
    cfg = tomllib.loads(raw)
    assert cfg["profile_overrides"]["centauri-carbon-pla-plus"] == {"fits.snug": 0.15, "holes.compensation": 0.18}


def test_profile_set_merges_with_existing(cli):
    cli("profile", "set", "fits.snug=0.15")
    cli("profile", "set", "fits.loose=0.6")
    cfg = tomllib.loads(cli.config.read_text(encoding="utf-8"))
    assert cfg["profile_overrides"]["centauri-carbon-pla-plus"] == {"fits.snug": 0.15, "fits.loose": 0.6}


@pytest.mark.parametrize("pair", ["fits.tight=0.1", "fits.snug=abc", "fits.snug=-1.5", "snug=0.1", "fits.snug"])
def test_invalid_set_writes_nothing(cli, pair):
    before = cli.config.read_bytes()
    code, data = cli("profile", "set", "fits.loose=0.6", pair)
    assert code == 2 and data["error"]["stage"] == "usage"
    assert cli.config.read_bytes() == before


def test_profile_reset_one_key_and_all(cli):
    cli("profile", "set", "fits.snug=0.15", "fits.loose=0.6")
    code, data = cli("profile", "reset", "fits.snug")
    assert code == 0 and data["values"]["fits.snug"]["source"] == "base"
    assert data["values"]["fits.loose"]["source"] == "override"
    code, data = cli("profile", "reset")
    assert code == 0
    assert "profile_overrides" not in tomllib.loads(cli.config.read_text(encoding="utf-8")) or \
        not tomllib.loads(cli.config.read_text(encoding="utf-8"))["profile_overrides"]


def test_profile_reset_unknown_override_is_usage_error(cli):
    code, data = cli("profile", "reset", "fits.snug")
    assert code == 2 and "fits.snug" in data["error"]["message"]


def test_run_uses_config_overrides(cli, tmp_path):
    cli("profile", "set", "holes.compensation=0.1")
    code, data = cli("run", model(tmp_path, "uses_profile.py"))
    assert code == 0, data
    assert data["profile"]["overrides"] == {"holes.compensation": 0.1}
    assert data["profile"]["used"]["holes.compensation"] == 0.1


def test_overrides_of_other_profiles_are_ignored(cli, tmp_path):
    cli.config.write_text(
        cli.config.read_text(encoding="utf-8")
        + '\n[profile_overrides.other-printer]\n"holes.compensation" = 0.9\n',
        encoding="utf-8",
    )
    code, data = cli("run", model(tmp_path, "uses_profile.py"))
    assert code == 0 and data["profile"]["overrides"] == {}


@pytest.mark.parametrize("args", [["run", "MODEL", "--profile", "nope"], ["profile", "show", "--profile", "nope"]])
def test_unknown_profile_is_usage_error_listing_profiles(cli, tmp_path, args):
    args = [str(model(tmp_path, "good_box.py")) if a == "MODEL" else a for a in args]
    code, data = cli(*args)
    assert code == 2
    assert "nope" in data["error"]["message"] and "centauri-carbon-pla-plus" in data["error"]["message"]


def test_bad_override_in_config_is_usage_error_naming_key(cli, tmp_path):
    cli.config.write_text(
        cli.config.read_text(encoding="utf-8") + '\n[profile_overrides.centauri-carbon-pla-plus]\n"fits.nope" = 1.0\n',
        encoding="utf-8",
    )
    code, data = cli("run", model(tmp_path, "good_box.py"))
    assert code == 2 and "fits.nope" in data["error"]["message"]
    assert "config.toml" in data["error"]["message"]


def test_unquoted_dotted_keys_in_config_are_flattened(cli):
    cli.config.write_text(
        cli.config.read_text(encoding="utf-8") + '\n[profile_overrides.centauri-carbon-pla-plus]\nfits.snug = 0.15\n',
        encoding="utf-8",
    )
    code, data = cli("profile", "show")
    assert code == 0, data
    assert data["values"]["fits.snug"] == {"value": 0.15, "source": "override"}


def test_config_profile_key_selects_profile(cli, tmp_path):
    cli.config.write_text(cli.config.read_text(encoding="utf-8") + 'profile = "nope"\n', encoding="utf-8")
    code, data = cli("profile", "show")
    assert code == 2 and "nope" in data["error"]["message"]


SUBTABLE = '\n[profile_overrides.centauri-carbon-pla-plus.fits]\nsnug = 0.15\n'


@pytest.mark.parametrize("args", [["set", "fits.loose=0.6"], ["reset", "fits.snug"], ["reset"]])
def test_subtable_overrides_are_refused_and_file_unchanged(cli, args):
    cli.config.write_text(cli.config.read_text(encoding="utf-8") + SUBTABLE, encoding="utf-8")
    before = cli.config.read_bytes()
    code, data = cli("profile", *args)
    assert code == 2 and "quoted" in data["error"]["message"]
    assert "config.toml" in data["error"]["message"]
    assert cli.config.read_bytes() == before
    code, data = cli("profile", "show")
    assert code == 0 and data["values"]["fits.snug"] == {"value": 0.15, "source": "override"}


def test_reset_with_no_config_file_creates_nothing(cli):
    cli.config.unlink()
    code, data = cli("profile", "reset")
    assert code == 0 and data["ok"] is True
    assert not cli.config.exists()


def test_quoted_name_subtable_is_refused_and_file_unchanged(cli):
    cli.config.write_text(
        cli.config.read_text(encoding="utf-8")
        + '\n[profile_overrides."centauri-carbon-pla-plus".fits]\nsnug = 0.15\n',
        encoding="utf-8",
    )
    before = cli.config.read_bytes()
    code, data = cli("profile", "reset", "fits.snug")
    assert code == 2 and data["ok"] is False
    assert cli.config.read_bytes() == before


def test_profile_set_negative_fit(cli):
    code, data = cli("profile", "set", "fits.press=-0.2")
    assert code == 0, data
    assert data["values"]["fits.press"] == {"value": -0.2, "source": "override"}
    stored = tomllib.loads(cli.config.read_text(encoding="utf-8"))
    assert stored["profile_overrides"]["centauri-carbon-pla-plus"]["fits.press"] == -0.2
    code, data = cli("profile", "show")
    assert data["values"]["fits.press"]["value"] == -0.2
