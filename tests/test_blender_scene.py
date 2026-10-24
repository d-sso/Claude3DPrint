"""blender_scene run inside a real Blender: the .blend layout, the stills and the result line.

Skipped when Blender isn't installed. Blender is found as `print3d render` finds it (render_scene.find_blender).
"""
import json
import struct
import subprocess
import textwrap
from pathlib import Path

import pytest

import render_scene
import scene_model as sm
from conftest import FIXTURES, SCRIPTS

pytestmark = pytest.mark.blender

SCRIPT = SCRIPTS / "blender_scene.py"
BOXLID = FIXTURES / "assembly" / "boxlid"
TIMEOUT_S = 900


BLENDER = render_scene.find_blender({})
if BLENDER is None:
    pytest.skip("Blender not found", allow_module_level=True)


def run_blender(*args, python=SCRIPT):
    proc = subprocess.run([str(BLENDER), "--background", "--factory-startup", "--python", str(python), "--",
                           *map(str, args)], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT_S)
    return proc


def result_of(proc):
    res = render_scene.parse_result(proc.stdout)
    assert res is not None, proc.stdout[-3000:] + proc.stderr[-3000:]
    return res


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
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT_S)
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


GLOW_UNIT = textwrap.dedent("""
    from build123d import Box

    from print3d_assembly import Assembly, StandIn, Unit, place


    def build_assembly(a):
        return Assembly([Unit("glow", [
            StandIn("strip", Box(18, 4, 2), color=(255, 200, 120), material="emissive", glow=20),
            StandIn("diffuser", Box(24, 10, 1), at=place(z=4), color=(240, 240, 240), material="translucent"),
        ])])
""")


def test_translucent_and_emissive_render(tmp_path, monkeypatch):
    monkeypatch.setenv("PRINT3D_CONFIG", str(tmp_path / "no-config.toml"))
    project = tmp_path / "glow"
    project.mkdir()
    (project / "assembly.py").write_text(GLOW_UNIT, encoding="utf-8")
    (group,), _ = sm.load_groups(project / "assembly.py", (), "all", None, None, None)
    assert {o.look.material for o in group.objects} == {"emissive", "translucent"}
    staged_json = sm.write_staging(group, tmp_path / "stage")
    pngs = {lighting: tmp_path / f"assembled-iso-{lighting}.png" for lighting in ("studio", "dim")}
    proc = run_blender(staged_json, "--resolution", "64x48", "--samples", "4", "--blend", tmp_path / "glow.blend",
                       *[a for lighting, png in pngs.items() for a in ("--still", f"assembled:iso:{lighting}={png}")])
    res = result_of(proc)
    assert res["ok"], res
    for png in pngs.values():
        assert png_size(png) == (64, 48)


UNDER_UNIT = textwrap.dedent("""
    from build123d import Box

    from print3d_assembly import Assembly, Screw, StandIn, Unit, place


    def build_assembly(a):
        return Assembly([Unit("under", [
            StandIn("plate", Box(30, 30, 4), at=place(z=2)),
            Screw("screw", "M3", "socket", 8.0, origin=(0, 0, 0), direction=(0, 0, -1)),
        ])])
""")

BACKDROP_INSPECT = textwrap.dedent("""
    import json
    import bpy
    from mathutils import Vector
    out = {}
    sc = bpy.data.scenes["studio"]
    for layer in sc.view_layers:
        lows, backdrops = [], []
        for o in sc.objects:
            if o.type != "MESH" or not o.visible_get(view_layer=layer):
                continue
            # matrix_basis, not matrix_world: a reloaded file's objects in collections excluded from the active
            # layer are never evaluated, so their matrix_world is stale; these objects have no parents
            zs = [(o.matrix_basis @ Vector(c)).z for c in o.bound_box]
            if o.name.startswith("backdrop"):
                backdrops.append([o.name, max(zs)])
            else:
                lows.append(min(zs))
        out[layer.name] = {"backdrops": backdrops, "lowest": min(lows)}
    print("INSPECT " + json.dumps(out))
""")


def test_backdrop_sits_under_each_state(tmp_path, monkeypatch):
    """A screw driven from below: its exploded pull and its driver go down, so each state needs its own floor."""
    monkeypatch.setenv("PRINT3D_CONFIG", str(tmp_path / "no-config.toml"))
    project = tmp_path / "under"
    project.mkdir()
    (project / "assembly.py").write_text(UNDER_UNIT, encoding="utf-8")
    (group,), _ = sm.load_groups(project / "assembly.py", (), "all", None, None, None)
    staged_json = sm.write_staging(group, tmp_path / "stage")
    blend = tmp_path / "under.blend"
    proc = run_blender(staged_json, "--resolution", "64x48", "--samples", "1", "--blend", blend,
                       "--still", f"assembled:iso:studio={tmp_path / 'a.png'}")
    assert result_of(proc)["ok"], proc.stdout[-3000:]
    script = tmp_path / "inspect.py"
    script.write_text(BACKDROP_INSPECT, encoding="utf-8")
    proc = subprocess.run([str(BLENDER), "--background", "--factory-startup", str(blend), "--python", str(script)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("INSPECT "))
    info = json.loads(line[len("INSPECT "):])
    assert set(info) == {"assembled", "exploded", "tools"}
    for state, layer in info.items():
        (backdrop,) = layer["backdrops"]  # exactly one visible backdrop per state
        assert backdrop[1] <= layer["lowest"] + 1e-6, (state, layer)
    assert info["exploded"]["lowest"] < info["assembled"]["lowest"]  # the screw really is pulled down
    assert info["tools"]["lowest"] < info["assembled"]["lowest"]  # and its driver points down
