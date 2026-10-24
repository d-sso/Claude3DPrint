"""blender_scene run inside a real Blender: the .blend layout, the stills and the result line.

Skipped when Blender isn't installed. Blender is found from PRINT3D_BLENDER, then the default install folder.
"""
import glob
import json
import os
import struct
import subprocess
import textwrap
from pathlib import Path

import pytest

import scene_model as sm
from conftest import FIXTURES, SCRIPTS

pytestmark = pytest.mark.blender

SCRIPT = SCRIPTS / "blender_scene.py"
BOXLID = FIXTURES / "assembly" / "boxlid"
TIMEOUT_S = 900


def _blender():
    env = os.environ.get("PRINT3D_BLENDER")
    if env:
        return Path(env) if Path(env).is_file() else None
    found = sorted(glob.glob("C:/Program Files/Blender Foundation/Blender */blender.exe"), reverse=True)
    return Path(found[0]) if found else None


BLENDER = _blender()
if BLENDER is None:
    pytest.skip("Blender not found", allow_module_level=True)


def run_blender(*args, python=SCRIPT):
    proc = subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(python), "--",
                           *map(str, args)], capture_output=True, text=True, timeout=TIMEOUT_S)
    return proc


def result_of(proc):
    lines = proc.stdout.strip().splitlines()
    assert lines and lines[-1].startswith("PRINT3D_RESULT "), proc.stdout[-3000:] + proc.stderr[-3000:]
    return json.loads(lines[-1][len("PRINT3D_RESULT "):])


def png_size(path):
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("blender")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("PRINT3D_CONFIG", str(tmp / "no-config.toml"))
        (group,), _ = sm.load_groups(BOXLID / "assembly.py", (), "all", None, None, None)
        return sm.write_staging(group, tmp / "stage")


@pytest.fixture(scope="module")
def default_run(staged):
    proc = run_blender(staged, "--resolution", "160x120", "--samples", "4")
    return proc, result_of(proc)


def test_blend_and_default_stills_written(staged, default_run):
    proc, res = default_run
    assert res["ok"] and res["error"] is None and proc.returncode == 0, res
    assert res["engine"] in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")
    assert len(res["stills"]) == 9
    for s in res["stills"]:
        assert Path(s["path"]).name == f"{s['state']}-{s['view']}-{s['lighting']}.png"
        assert Path(s["path"]).parent == staged.parent
        assert png_size(s["path"]) == (160, 120)
        assert s["seconds"] > 0
    assert Path(res["blend"]) == staged.parent / "scene.blend" and Path(res["blend"]).is_file()


INSPECT = textwrap.dedent("""
    import json
    import bpy
    out = {
        "scenes": sorted(s.name for s in bpy.data.scenes),
        "layers": {s.name: sorted(v.name for v in s.view_layers) for s in bpy.data.scenes},
        "assembled": len(bpy.data.collections["assembled"].objects),
        "cameras": sorted(o.name for o in bpy.data.objects if o.type == "CAMERA"),
    }
    print("INSPECT " + json.dumps(out))
""")


def test_blend_structure(staged, default_run, tmp_path):
    _, res = default_run
    script = tmp_path / "inspect.py"
    script.write_text(INSPECT, encoding="utf-8")
    proc = subprocess.run([str(BLENDER), "--background", "--factory-startup", res["blend"], "--python", str(script)],
                          capture_output=True, text=True, timeout=TIMEOUT_S)
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("INSPECT "))
    info = json.loads(line[len("INSPECT "):])
    assert set(info["scenes"]) == {"studio", "hard", "dim"}
    for layers in info["layers"].values():
        assert set(layers) == {"assembled", "exploded", "tools"}
    scene = json.loads(staged.read_text(encoding="utf-8"))
    assert info["assembled"] == sum(o["collection"] != "tools" for o in scene["objects"])
    assert info["cameras"] == ["cam-front", "cam-iso", "cam-right", "cam-tools", "cam-top"]


def test_exploded_still_differs_from_assembled(default_run):
    _, res = default_run
    paths = {(s["state"], s["view"], s["lighting"]): Path(s["path"]) for s in res["stills"]}
    assert (paths["exploded", "iso", "studio"].read_bytes()
            != paths["assembled", "iso", "studio"].read_bytes())


def test_bad_scene_json_reports_error(staged, tmp_path):
    scene = json.loads(staged.read_text(encoding="utf-8"))
    scene["meshes"] = {k: "meshes/missing.stl" for k in scene["meshes"]}
    bad = tmp_path / "scene.json"
    bad.write_text(json.dumps(scene), encoding="utf-8")
    proc = run_blender(bad, "--resolution", "160x120", "--samples", "4")
    res = result_of(proc)
    assert proc.returncode == 1
    assert res["ok"] is False and "missing.stl" in res["error"]


def test_final_quality_uses_cycles(staged, tmp_path):
    png = tmp_path / "final.png"
    blend = tmp_path / "final.blend"
    proc = run_blender(staged, "--quality", "final", "--resolution", "64x48", "--samples", "1",
                       "--still", f"assembled:iso:studio={png}", "--blend", blend)
    res = result_of(proc)
    assert res["ok"], res
    assert res["engine"] == "CYCLES" and res["device"]
    assert [s["path"] for s in res["stills"]] == [str(png)]
    assert png_size(png) == (64, 48) and blend.is_file()
