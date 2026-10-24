"""print3d render: the command, its result, Blender lookup and failures, with a fake Blender (and one real run)."""
import json
import shutil
import sys
import types
from pathlib import Path

import pytest
from conftest import FIXTURES, SCRIPTS

import print3d
import render_scene

BOXLID = FIXTURES / "assembly" / "boxlid"
ROW = FIXTURES / "assembly" / "row"
FAKE = FIXTURES / "fake_blender.py"
KEYS = {"ok", "model", "parameters", "overrides", "shared", "profile", "quality", "blender", "groups", "warnings",
        "timings_s", "error"}
GROUP_KEYS = {"kind", "name", "folder", "blend", "images", "tool_access", "warnings"}


@pytest.fixture(autouse=True)
def no_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINT3D_CONFIG", str(tmp_path / "no-config.toml"))
    monkeypatch.delenv("FAKE_BLENDER_FAIL", raising=False)


@pytest.fixture
def fake_blender(monkeypatch):
    """Blender is the fake script, run with the venv's Python (blender_scene importable from PYTHONPATH)."""
    monkeypatch.setenv("PYTHONPATH", str(SCRIPTS))
    def use(path=FAKE):
        monkeypatch.setattr(render_scene, "find_blender", lambda config: Path(path))
        monkeypatch.setattr(render_scene, "blender_command", lambda blender: [sys.executable, str(blender)])
        return Path(path)
    use()
    return use


def render(capsys, *args):
    code = print3d.main(["render", *map(str, args)])
    return code, json.loads(capsys.readouterr().out)


def test_render_boxlid_cli(capsys, tmp_path, fake_blender):
    out = tmp_path / "out"
    code, data = render(capsys, BOXLID / "assembly.py", "--out", out)
    assert code == 0, data["error"]
    assert set(data) == KEYS and data["ok"] is True and data["error"] is None
    assert data["quality"]["name"] == "draft" and data["blender"]["version"] == "4.5.0 (fake)"
    assert data["blender"]["path"] == FAKE.resolve().as_posix()
    assert data["profile"]["name"] and data["parameters"] == {"screw_len": 12.0}
    (group,) = data["groups"]
    assert set(group) == GROUP_KEYS
    assert (group["kind"], group["name"], group["folder"]) == ("unit", "box", "unit-box")
    folder = (out / "render" / "unit-box").resolve()
    assert len(group["images"]) == 9
    for image in group["images"]:
        assert set(image) == {"state", "view", "lighting", "path"}
        path = Path(image["path"])
        assert path.is_file() and path.parent == folder
        assert path.name == f"{image['state']}-{image['view']}-{image['lighting']}.png"
    assert Path(group["blend"]) == folder / "scene.blend" and Path(group["blend"]).is_file()
    assert [e["status"] for e in group["tool_access"]] == ["clear", "clear"]
    assert not (folder / "_staging").exists()
    saved = json.loads((out / "render.result.json").read_text(encoding="utf-8"))
    assert saved["groups"] == data["groups"]


def test_render_model_cli(capsys, tmp_path, fake_blender):
    code, data = render(capsys, FIXTURES / "good_box.py", "--out", tmp_path / "out")
    assert code == 0, data["error"]
    (group,) = data["groups"]
    assert (group["kind"], group["folder"]) == ("model", "good_box")
    assert len(group["images"]) == 6
    assert {i["state"] for i in group["images"]} == {"assembled"}
    assert "good_box: no screws, so no tools view" in group["warnings"]
    assert "good_box: no screws, so no tools view" in data["warnings"]
    assert group["tool_access"] == []


def test_render_filters(capsys, tmp_path, fake_blender):
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path, "--state", "exploded", "--view", "front")
    assert code == 0, data["error"]
    (image,) = data["groups"][0]["images"]
    assert Path(image["path"]).name == "exploded-front-studio.png"
    assert sorted(p.name for p in (tmp_path / "render" / "unit-box").glob("*.png")) == ["exploded-front-studio.png"]


def test_render_unit_and_layout_selection(capsys, tmp_path, fake_blender):
    code, data = render(capsys, ROW / "assembly.py", "--out", tmp_path, "--level", "layout")
    assert code == 0, data["error"]
    assert [g["folder"] for g in data["groups"]] == ["layout-row"]
    code, data = render(capsys, ROW / "assembly.py", "--out", tmp_path, "--unit", "cell")
    assert code == 0, data["error"]
    assert [g["folder"] for g in data["groups"]] == ["unit-cell"]


def test_render_paths_with_spaces(capsys, tmp_path, fake_blender):
    fake_dir = tmp_path / "Blender Foundation"
    fake_dir.mkdir()
    fake_blender(shutil.copy(FAKE, fake_dir / "fake blender.py"))
    out = tmp_path / "with space" / "out"
    code, data = render(capsys, BOXLID / "assembly.py", "--out", out, "--state", "assembled", "--view", "iso")
    assert code == 0, data["error"]
    (image,) = data["groups"][0]["images"]
    assert Path(image["path"]).is_file() and "with space" in image["path"]
    assert data["blender"]["path"].endswith("Blender Foundation/fake blender.py")


def test_blender_missing(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(render_scene, "find_blender", lambda config: None)
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path)
    assert code == 1 and data["ok"] is False
    assert data["error"]["stage"] == "blender"
    assert "winget install BlenderFoundation.Blender" in data["error"]["message"]
    assert data["error"]["message"] == render_scene.MISSING_MESSAGE
    assert (tmp_path / "render.result.json").is_file()


@pytest.mark.parametrize("args, message", [
    (("missing.py",), "File not found"),
    (("README.txt",), "Expected a .py file"),
    ((BOXLID / "assembly.py", "--unit", "nope"), "Unknown unit 'nope'"),
    ((BOXLID / "assembly.py", "--unit", "box", "--level", "layout"), "--unit cannot be combined"),
])
def test_usage_errors_come_before_the_blender_lookup(capsys, tmp_path, monkeypatch, args, message):
    monkeypatch.setattr(render_scene, "find_blender", lambda config: None)
    (tmp_path / "README.txt").write_text("not a model", encoding="utf-8")
    args = [tmp_path / a if a in ("missing.py", "README.txt") else a for a in args]
    code, data = render(capsys, *args, "--out", tmp_path)
    assert code == 2 and data["error"]["stage"] == "usage", data["error"]
    assert message in data["error"]["message"]


def test_blender_failure_reports_log_and_leaves_no_stale_images(capsys, tmp_path, fake_blender, monkeypatch):
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path)
    assert code == 0, data["error"]
    folder = tmp_path / "render" / "unit-box"
    assert len(list(folder.glob("*.png"))) == 9 and (folder / "scene.blend").is_file()
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "exit")
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path)
    assert code == 1 and data["ok"] is False
    assert data["error"]["stage"] == "blender"
    assert "fake blender: render crashed" in data["error"]["message"]
    assert "exit code 3" in data["error"]["message"]
    assert len(list(folder.glob("*.png"))) <= 1
    assert not (folder / "scene.blend").exists()
    assert (folder / "_staging" / "scene.json").is_file()  # kept for debugging


def test_failure_on_first_group_leaves_no_stale_outputs_in_later_groups(capsys, tmp_path, fake_blender,
                                                                       monkeypatch):
    code, data = render(capsys, ROW / "assembly.py", "--out", tmp_path)
    assert code == 0, data["error"]
    folders = [tmp_path / "render" / g["folder"] for g in data["groups"]]
    assert [f.name for f in folders] == ["unit-cell", "layout-row"]
    for folder in folders:
        assert list(folder.glob("*.png")) and (folder / "scene.blend").is_file()
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "exit")
    code, data = render(capsys, ROW / "assembly.py", "--out", tmp_path)
    assert code == 1 and data["error"]["stage"] == "blender" and "unit-cell" in data["error"]["message"]
    assert len(list(folders[0].glob("*.png"))) <= 1  # the one the failing run wrote
    for folder in folders:
        assert not (folder / "scene.blend").exists(), folder.name
    assert not list(folders[1].glob("*.png"))  # never rendered this run, and nothing left from the first


def test_blender_without_result_line_fails(capsys, tmp_path, fake_blender, monkeypatch):
    monkeypatch.setenv("FAKE_BLENDER_FAIL", "noresult")
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path)
    assert code == 1 and data["error"]["stage"] == "blender"
    assert "PRINT3D_RESULT" in data["error"]["message"]


def test_parse_result_takes_last_result_line():
    stdout = 'PRINT3D_RESULT {"ok": false}\nnoise\nPRINT3D_RESULT {"ok": true}\nBlender quit\n'
    assert render_scene.parse_result(stdout) == {"ok": True}
    assert render_scene.parse_result("Blender quit\n") is None
    assert render_scene.parse_result("PRINT3D_RESULT {not json\n") is None


def _exe(root, name):
    path = root / name / "blender.exe"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    return path


def test_find_blender_order_and_min_version(tmp_path, monkeypatch):
    monkeypatch.delenv("PRINT3D_BLENDER", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    found = tmp_path / "pf" / "Blender Foundation"
    old = _exe(found, "Blender 4.1")
    assert render_scene.find_blender({}) is None  # 4.1 is below MIN_BLENDER
    new = _exe(found, "Blender 4.5")
    _exe(found, "Blender 4.10")
    assert render_scene.find_blender({}) == found / "Blender 4.10" / "blender.exe"  # by version, not by name
    shutil.rmtree(found / "Blender 4.10")
    assert render_scene.find_blender({}) == new
    env = _exe(tmp_path / "env", "Blender 4.3")
    monkeypatch.setenv("PRINT3D_BLENDER", str(env))
    assert render_scene.find_blender({}) == env
    configured = _exe(tmp_path / "cfg", "Blender 4.2")
    assert render_scene.find_blender({"blender": str(configured)}) == configured
    assert render_scene.find_blender({"blender": str(tmp_path / "missing.exe")}) == env
    monkeypatch.setenv("PRINT3D_BLENDER", str(old))
    assert render_scene.find_blender({}) == new  # too old: skipped


def test_bad_resolution_is_usage(capsys, tmp_path, fake_blender):
    for bad in ("160", "160x", "axb", "160x120x3", "0x120"):
        code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path, "--resolution", bad)
        assert code == 2 and data["error"]["stage"] == "usage", bad
    assert not (tmp_path / "render.result.json").exists()


def test_render_passes_resolution_and_quality(capsys, tmp_path, fake_blender, monkeypatch):
    seen = []
    real_run = render_scene.subprocess.run

    def spy(cmd, **kwargs):
        seen.append(cmd)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(render_scene.subprocess, "run", spy)
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path, "--final", "--resolution", "160x120",
                        "--view", "top")
    assert code == 0, data["error"]
    (cmd,) = seen
    tail = cmd[cmd.index("--") + 1:]
    assert tail[tail.index("--quality") + 1] == "final" and tail[tail.index("--resolution") + 1] == "160x120"
    assert data["quality"]["name"] == "final" and data["quality"]["resolution"] == [160, 120]


def test_render_open_launches_blender_on_the_blend(capsys, tmp_path, fake_blender, monkeypatch):
    opened = []
    sp = render_scene.subprocess
    # a stand-in for the subprocess module in render_scene only: its run() still uses the real Popen, and nothing
    # else (modules imported during load) sees the spy
    spy = types.SimpleNamespace(**vars(sp))
    spy.Popen = lambda cmd, **kwargs: opened.append((cmd, kwargs))
    monkeypatch.setattr(render_scene, "subprocess", spy)
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path, "--view", "iso", "--open")
    assert code == 0, data["error"]
    ((cmd, kwargs),) = opened
    assert cmd == [str(FAKE), str(Path(data["groups"][0]["blend"]))]
    # detached from the CLI's std handles (stdout carries exactly one JSON document) and from its process tree
    assert kwargs["stdin"] == kwargs["stdout"] == kwargs["stderr"] == sp.DEVNULL
    if render_scene.os.name == "nt":
        assert kwargs["creationflags"] == sp.DETACHED_PROCESS | sp.CREATE_NEW_PROCESS_GROUP
    else:
        assert "creationflags" not in kwargs


def test_render_does_not_touch_assembly_outputs(capsys, tmp_path, fake_blender):
    assembly_result = tmp_path / "assembly.result.json"
    assembly_result.write_text('{"status": "pass"}', encoding="utf-8")
    sheet = tmp_path / "assembly" / "box" / "overview.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"png")
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path)
    assert code == 0, data["error"]
    assert assembly_result.read_text(encoding="utf-8") == '{"status": "pass"}'
    assert sheet.read_bytes() == b"png"


@pytest.mark.blender
def test_render_boxlid_real_blender(capsys, tmp_path):
    if render_scene.find_blender({}) is None:
        pytest.skip("Blender not found")
    code, data = render(capsys, BOXLID / "assembly.py", "--out", tmp_path, "--resolution", "160x120")
    assert code == 0, data["error"]
    (group,) = data["groups"]
    assert len(group["images"]) == 9 and all(Path(i["path"]).stat().st_size > 0 for i in group["images"])
    assert Path(group["blend"]).is_file()
    assert data["blender"]["version"].startswith("4.")
