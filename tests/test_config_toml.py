import tomllib

import pytest

from config_toml import write_profile_overrides

ORIGINAL = (
    "# my config\r\n"
    'parts_dir = "C:/x/3DParts"  # keep me\r\n'
    'profile = "centauri-carbon-pla-plus"\r\n'
    "\r\n"
    "[profile_overrides.centauri-carbon-pla-plus]\r\n"
    '"fits.snug" = 0.15\r\n'
    "\r\n"
    "[profile_overrides.other-printer]\r\n"
    '"fits.snug" = 0.4\r\n'
    "\r\n"
    "[checks]\r\n"
    "min_wall = 1.0\r\n"
)


def read_bytes(p):
    return p.read_bytes().decode("utf-8")


def test_replaces_only_the_named_block_and_keeps_crlf(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(ORIGINAL.encode("utf-8"))
    write_profile_overrides(cfg, "centauri-carbon-pla-plus", {"holes.compensation": 0.18, "fits.snug": 0.15})
    text = read_bytes(cfg)
    assert text.startswith("# my config\r\nparts_dir = \"C:/x/3DParts\"  # keep me\r\n")
    assert "[profile_overrides.other-printer]\r\n\"fits.snug\" = 0.4\r\n" in text
    assert text.endswith("[checks]\r\nmin_wall = 1.0\r\n")
    data = tomllib.loads(text)
    assert data["profile_overrides"]["centauri-carbon-pla-plus"] == {"fits.snug": 0.15, "holes.compensation": 0.18}
    assert data["profile_overrides"]["other-printer"] == {"fits.snug": 0.4}
    assert data["parts_dir"] == "C:/x/3DParts"


def test_keys_written_sorted_and_quoted(tmp_path):
    cfg = tmp_path / "config.toml"
    write_profile_overrides(cfg, "p1", {"inserts.M2.5": 3.5, "fits.press": 0.05})
    lines = read_bytes(cfg).splitlines()
    assert lines[-3:] == ["[profile_overrides.p1]", '"fits.press" = 0.05', '"inserts.M2.5" = 3.5']


def test_appends_block_when_missing(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'parts_dir = "C:/x"\n')
    write_profile_overrides(cfg, "centauri-carbon-pla-plus", {"fits.loose": 0.6})
    text = read_bytes(cfg)
    assert text.startswith('parts_dir = "C:/x"\n')
    assert tomllib.loads(text)["profile_overrides"]["centauri-carbon-pla-plus"] == {"fits.loose": 0.6}


def test_empty_values_remove_block(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(ORIGINAL.encode("utf-8"))
    write_profile_overrides(cfg, "centauri-carbon-pla-plus", {})
    data = tomllib.loads(read_bytes(cfg))
    assert "centauri-carbon-pla-plus" not in data["profile_overrides"]
    assert data["profile_overrides"]["other-printer"] == {"fits.snug": 0.4}
    assert data["checks"] == {"min_wall": 1.0}


def test_empty_values_without_block_leaves_file_unchanged(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b'parts_dir = "C:/x"\r\n')
    write_profile_overrides(cfg, "centauri-carbon-pla-plus", {})
    assert cfg.read_bytes() == b'parts_dir = "C:/x"\r\n'


def test_creates_missing_file(tmp_path):
    cfg = tmp_path / "sub" / "config.toml"
    write_profile_overrides(cfg, "p1", {"fits.snug": 0.2})
    assert tomllib.loads(read_bytes(cfg))["profile_overrides"]["p1"] == {"fits.snug": 0.2}


def test_invalid_result_restores_original(tmp_path, monkeypatch):
    import config_toml

    cfg = tmp_path / "config.toml"
    cfg.write_bytes(ORIGINAL.encode("utf-8"))
    monkeypatch.setattr(config_toml, "_render_block", lambda name, values, eol: ["[[broken\n"])
    with pytest.raises(ValueError):
        write_profile_overrides(cfg, "centauri-carbon-pla-plus", {"fits.snug": 0.2})
    assert read_bytes(cfg) == ORIGINAL


TRAILING_COMMENT = (
    "[profile_overrides.a]\r\n"
    '"fits.snug" = 0.1\r\n'
    "# note about b\r\n"
    "\r\n"
    "[profile_overrides.b]\r\n"
    '"fits.snug" = 0.4\r\n'
)


def test_failed_replace_keeps_original_and_leaves_no_temp_file(tmp_path, monkeypatch):
    import config_toml

    cfg = tmp_path / "config.toml"
    cfg.write_bytes(ORIGINAL.encode("utf-8"))

    def fail_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(config_toml.os, "replace", fail_replace)
    with pytest.raises(OSError):
        write_profile_overrides(cfg, "centauri-carbon-pla-plus", {"fits.snug": 0.2})
    assert read_bytes(cfg) == ORIGINAL
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml"]


def test_replace_keeps_comment_and_blank_before_next_table(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(TRAILING_COMMENT.encode("utf-8"))
    write_profile_overrides(cfg, "a", {"fits.snug": 0.2})
    text = read_bytes(cfg)
    assert text.endswith("# note about b\r\n\r\n[profile_overrides.b]\r\n\"fits.snug\" = 0.4\r\n")
    data = tomllib.loads(text)
    assert data["profile_overrides"]["a"] == {"fits.snug": 0.2}
    assert data["profile_overrides"]["b"] == {"fits.snug": 0.4}


def test_remove_keeps_comment_and_blank_before_next_table(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(TRAILING_COMMENT.encode("utf-8"))
    write_profile_overrides(cfg, "a", {})
    assert read_bytes(cfg) == "# note about b\r\n\r\n[profile_overrides.b]\r\n\"fits.snug\" = 0.4\r\n"


def test_replace_before_single_blank_leaves_one_blank_line(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(
        b'[profile_overrides.a]\r\n"fits.snug" = 0.1\r\n\r\n[profile_overrides.b]\r\n"fits.snug" = 0.4\r\n'
    )
    write_profile_overrides(cfg, "a", {"fits.snug": 0.2})
    assert read_bytes(cfg) == (
        '[profile_overrides.a]\r\n"fits.snug" = 0.2\r\n\r\n[profile_overrides.b]\r\n"fits.snug" = 0.4\r\n'
    )
