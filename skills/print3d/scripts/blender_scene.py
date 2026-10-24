"""Blender-side scene script for `print3d render`.

Runs inside Blender's own Python, so module level imports only the standard library. `bpy` and
`mathutils` are imported inside functions; never import build123d or other print3d modules here.
The first part holds the pure helpers (still list, camera directions and distance, colour
conversion, argument parsing), testable in the venv. The second part builds the .blend and renders:

    blender --background --factory-startup --python blender_scene.py -- scene.json [--blend PATH]
        [--still STATE:VIEW:LIGHTING=PATH ...] [--quality draft|final] [--resolution WxH] [--samples N]

The last stdout line is `PRINT3D_RESULT <json>`. The .blend's layout (what the user opens and orbits):
- collections `assembled` (one object per scene object, mesh data shared per mesh id), `exploded` (linked
  duplicates moved by `explode`, only when some offset is non-zero), `tools`, `backdrop` and `cameras`;
- scenes `studio`, `hard` and `dim`, one per lighting, each linking those collections plus its own
  `lights-<lighting>` collection and World;
- view layers `assembled`, `exploded` and `tools` in every scene, one per state: switch layers to switch state;
- cameras `cam-<view>`, framed on the assembled state (`cam-tools` on the tools state). Each still reframes its
  camera on its own state's bbox.
"""
import argparse
import itertools
import math

VIEWS = ("iso", "front", "right", "top", "tools")
LIGHTINGS = ("studio", "hard", "dim")
STATES = ("assembled", "exploded", "tools")

DEFAULT_STILLS = (
    ("assembled", "iso", "studio"),
    ("assembled", "iso", "hard"),
    ("assembled", "iso", "dim"),
    ("assembled", "front", "studio"),
    ("assembled", "right", "studio"),
    ("assembled", "top", "studio"),
    ("exploded", "iso", "studio"),
    ("exploded", "front", "studio"),
    ("tools", "tools", "studio"),
)

QUALITY = {
    "draft": {"engine": "eevee", "resolution": (1280, 960), "samples": 32},
    "final": {"engine": "cycles", "resolution": (1920, 1440), "samples": 256},
}


def still_plan(views, lightings, states, *, has_tools, has_explode):
    """The (state, view, lighting) stills to render; empty axes fall back to defaults."""
    if not (views or lightings or states):
        plan = list(DEFAULT_STILLS)
    else:
        plan = list(itertools.product(states or ("assembled",), views or ("iso",),
                                      lightings or ("studio",)))
    return [s for s in plan
            if (has_tools or s[0] != "tools") and (has_explode or s[0] != "exploded")]


def still_name(state, view, lighting):
    return f"{state}-{view}-{lighting}.png"


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def view_direction(view, backdrop, facing):
    """(dir, up): dir is the unit vector from the target to the camera, up the image's up vector."""
    if view not in VIEWS:
        raise ValueError(f"unknown view {view!r}")
    if backdrop != "wall":
        return {
            "front": ((0, -1, 0), (0, 0, 1)),
            "right": ((1, 0, 0), (0, 0, 1)),
            "top": ((0, 0, 1), (0, 1, 0)),
        }.get(view, (_norm((1, -1, 0.8)), (0, 0, 1)))
    face = _norm(facing)
    up = (0, 1, 0) if abs(face[2]) > 0.999 else (0, 0, 1)
    right = _cross(up, face)  # facing turned 90 degrees about +Z
    if view == "front":
        return face, up
    if view == "right":
        return right, up
    if view == "top":
        return up, tuple(-c for c in face)
    return _norm(tuple(f + 0.6 * r + 0.5 * u for f, r, u in zip(face, right, up))), up


def fit_distance(radius, lens_mm=50.0, sensor_mm=36.0, aspect=4 / 3, margin=1.1):
    """Camera distance at which a sphere of `radius` fills the narrower field of view, times margin."""
    narrow = sensor_mm / 2 * min(1.0, 1.0 / aspect)  # sensor_mm is the width
    return radius / math.sin(math.atan(narrow / lens_mm)) * margin


def srgb_to_linear(rgb255):
    """sRGB 0-255 triple to linear 0-1 (IEC 61966-2-1 transfer)."""
    out = []
    for c in rgb255:
        c = c / 255.0
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return tuple(out)


def _still_spec(text):
    left, sep, path = text.partition("=")
    parts = left.split(":")
    if not sep or len(parts) != 3 or not path:
        raise argparse.ArgumentTypeError(f"expected STATE:VIEW:LIGHTING=PATH, got {text!r}")
    return (*parts, path)


def _resolution(text):
    try:
        w, h = text.lower().split("x")
        return int(w), int(h)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WxH, got {text!r}") from None


def parse_args(argv):
    """Parse the arguments after `--` on Blender's command line."""
    p = argparse.ArgumentParser(prog="blender_scene")
    p.add_argument("scene_json")
    p.add_argument("--blend")
    p.add_argument("--still", action="append", default=[], type=_still_spec)
    p.add_argument("--quality", choices=sorted(QUALITY), default="draft")
    p.add_argument("--resolution", type=_resolution)
    p.add_argument("--samples", type=int)
    return p.parse_args(argv)


# --- the bpy part: everything below runs inside Blender ----------------------------------------------------------

MM = 0.001  # scene.json is in mm, the .blend in metres
SMOOTH_ANGLE_DEG = 30.0
LENS_MM = 50.0
BACKDROP_RGB = (235, 235, 235)
BACKDROP_SCALE = 20.0  # the backdrop plane's size, times the bbox radius
WORLD_GREY = (0.3, 0.3, 0.3)
KEY_ENERGY_PER_M2 = 400.0  # studio key light energy (W) per square metre of bbox radius
KEY_TURN_DEG = 40.0  # the key light: the iso direction turned about up
FILL_TURN_DEG = -70.0
FILL_FACTOR = 0.3
LIGHT_DISTANCE = 4.0  # light distance from the bbox centre, times the radius
DIM_FACTOR = 0.05
SUN_STRENGTH = 4.0
SUN_ANGLE_DEG = 0.5
WORLD_STRENGTH = {"studio": 0.5, "hard": 0.05, "dim": 0.02}
GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL")
OBJECT_COLLECTIONS = ("assembled", "exploded", "tools", "backdrop", "cameras")
LAYER_SHOWS = {  # view layer (state) -> the object collections it shows; cameras show everywhere
    "assembled": ("assembled", "backdrop"),
    "exploded": ("exploded", "backdrop"),
    "tools": ("assembled", "tools", "backdrop"),
}


def _bbox_of(objs):
    from mathutils import Vector
    pts = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
    if not pts:
        return None
    return (Vector([min(p[i] for p in pts) for i in range(3)]), Vector([max(p[i] for p in pts) for i in range(3)]))


def _union(*boxes):
    from mathutils import Vector
    boxes = [b for b in boxes if b]
    return (Vector([min(b[0][i] for b in boxes) for i in range(3)]),
            Vector([max(b[1][i] for b in boxes) for i in range(3)]))


def _centre_radius(box):
    lo, hi = box
    return (lo + hi) / 2, max((hi - lo).length / 2, 1e-4)


def _material(spec):
    """A Principled BSDF material for (kind, rgb, glow)."""
    import bpy
    kind, rgb, glow = spec
    mat = bpy.data.materials.new(f"{kind}-{'-'.join(map(str, rgb))}" + (f"-glow{glow}" if glow else ""))
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    colour = (*srgb_to_linear(rgb), 1.0)
    inputs = bsdf.inputs
    inputs["Base Color"].default_value = colour
    mat.diffuse_color = colour  # the solid-mode viewport colour
    if kind == "metal":
        inputs["Metallic"].default_value = 1.0
        inputs["Roughness"].default_value = 0.35
    elif kind == "translucent":
        inputs["Transmission Weight"].default_value = 1.0
        inputs["Roughness"].default_value = 0.45
        if hasattr(mat, "use_raytrace_refraction"):
            mat.use_raytrace_refraction = True
    else:
        inputs["Roughness"].default_value = 0.6
        inputs["Specular IOR Level"].default_value = 0.3
        if kind == "emissive":
            inputs["Emission Color"].default_value = colour
            inputs["Emission Strength"].default_value = float(glow if glow is not None else 1.0)
    return mat


def _import_meshes(scene, folder):
    """One mesh datablock per mesh id, in its local frame and in metres, smooth below SMOOTH_ANGLE_DEG."""
    import bpy
    from pathlib import Path
    meshes = {}
    for mesh_id, rel in scene["meshes"].items():
        path = Path(folder) / rel
        if not path.is_file():
            raise FileNotFoundError(f"mesh file not found: {path.as_posix()} (mesh {mesh_id})")
        before = set(bpy.data.objects)
        bpy.ops.wm.stl_import(filepath=str(path), global_scale=MM)
        new = [o for o in bpy.data.objects if o not in before]
        if len(new) != 1 or new[0].type != "MESH":
            raise RuntimeError(f"STL import of {path.as_posix()} gave {len(new)} objects")
        mesh = new[0].data
        mesh.transform(new[0].matrix_world)  # the importer puts global_scale on the object; keep it in the mesh
        bpy.data.objects.remove(new[0])
        mesh.name = mesh_id
        mesh.shade_smooth()
        mesh.set_sharp_from_angle(angle=math.radians(SMOOTH_ANGLE_DEG))
        mesh.materials.append(None)  # one slot; each object links its own material to it
        meshes[mesh_id] = mesh
    return meshes


def _matrix(rows, offset=(0.0, 0.0, 0.0)):
    from mathutils import Matrix
    m = Matrix([list(r) for r in rows])
    for i in range(3):
        m[i][3] = (m[i][3] + offset[i]) * MM
    return m


def _new_object(name, mesh, mat, matrix, coll):
    import bpy
    obj = bpy.data.objects.new(name, mesh)
    obj.material_slots[0].link = "OBJECT"
    obj.material_slots[0].material = mat
    obj.matrix_world = matrix
    coll.objects.link(obj)
    return obj


def _build_objects(scene, meshes, colls):
    """Assembled, exploded (only when something moves) and tool objects, per collection name."""
    import bpy
    mats = {}
    out = {"assembled": [], "exploded": [], "tools": []}
    explodes = any(any(o["explode"]) for o in scene["objects"] if o["collection"] != "tools")
    for o in scene["objects"]:
        spec = (o["material"], tuple(o["rgb"]), o["glow"])
        if spec not in mats:
            mats[spec] = _material(spec)
        target = "tools" if o["collection"] == "tools" else "assembled"
        mesh = meshes[o["mesh"]]
        out[target].append(_new_object(o["name"], mesh, mats[spec], _matrix(o["matrix"]), colls[target]))
        if target == "assembled" and explodes:
            out["exploded"].append(_new_object(f"{o['name']}.exploded", mesh, mats[spec],
                                               _matrix(o["matrix"], o["explode"]), colls["exploded"]))
    if not explodes:
        bpy.data.collections.remove(colls.pop("exploded"))
    return out


def _backdrop(scene, box, coll):
    """A matte plane: a floor under the assembled bbox, or a wall behind it with its normal along `facing`."""
    import bpy
    from mathutils import Vector
    centre, r = _centre_radius(box)
    half = BACKDROP_SCALE * r / 2
    mesh = bpy.data.meshes.new("backdrop")
    mesh.from_pydata([(-half, -half, 0), (half, -half, 0), (half, half, 0), (-half, half, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(_material(("matte", BACKDROP_RGB, None)))
    obj = bpy.data.objects.new("backdrop", mesh)
    if scene["backdrop"] == "wall":
        normal = Vector(scene["facing"]).normalized()
        corners = [Vector((x, y, z)) for x in (box[0].x, box[1].x) for y in (box[0].y, box[1].y)
                   for z in (box[0].z, box[1].z)]
        depth = min((c - centre).dot(normal) for c in corners)
        obj.rotation_euler = Vector((0, 0, 1)).rotation_difference(normal).to_euler()
        obj.location = centre + normal * depth
    else:
        obj.location = (centre.x, centre.y, box[0].z)
    coll.objects.link(obj)


def _aim(obj, target, direction, up, distance):
    """Put obj at target + direction * distance, its -Z towards the target and its +Y along `up`."""
    from mathutils import Matrix, Vector
    z = Vector(direction).normalized()
    x = Vector(up).cross(z).normalized()
    y = z.cross(x)
    rot = Matrix((x, y, z)).transposed().to_4x4()
    obj.matrix_world = Matrix.Translation(target + z * distance) @ rot


def _frame(cam, view, box, scene, aspect):
    """Aim a camera at the bbox centre from `view`, at the distance that fits the bbox's sphere."""
    centre, r = _centre_radius(box)
    direction, up = view_direction(view, scene["backdrop"], scene["facing"])
    _aim(cam, centre, direction, up, fit_distance(r, LENS_MM, cam.data.sensor_width, aspect))
    cam.data.clip_start = 0.001 * r
    cam.data.clip_end = 100 * r


def _cameras(coll):
    import bpy
    cams = {}
    for view in VIEWS:
        data = bpy.data.cameras.new(f"cam-{view}")
        data.lens = LENS_MM
        data.sensor_fit = "HORIZONTAL"
        cams[view] = bpy.data.objects.new(f"cam-{view}", data)
        coll.objects.link(cams[view])
    return cams


def _turned(direction, up, degrees):
    from mathutils import Matrix, Vector
    return Matrix.Rotation(math.radians(degrees), 3, Vector(up)) @ Vector(direction)


def _light(name, kind, coll, centre, r, direction, up, energy):
    import bpy
    data = bpy.data.lights.new(name, kind)
    data.energy = energy
    if kind == "AREA":
        data.size = r
    else:
        data.angle = math.radians(SUN_ANGLE_DEG)
    obj = bpy.data.objects.new(name, data)
    _aim(obj, centre, direction, up, LIGHT_DISTANCE * r)
    coll.objects.link(obj)


def _lights(lighting, coll, scene, box):
    """The lights of one lighting setup, sized and placed from the assembled bbox."""
    centre, r = _centre_radius(box)
    iso, up = view_direction("iso", scene["backdrop"], scene["facing"])
    key_dir = _turned(iso, up, KEY_TURN_DEG)
    key = KEY_ENERGY_PER_M2 * r * r
    if lighting == "studio":
        _light(f"key-{lighting}", "AREA", coll, centre, r, key_dir, up, key)
        _light(f"fill-{lighting}", "AREA", coll, centre, r, _turned(iso, up, FILL_TURN_DEG), up, key * FILL_FACTOR)
    elif lighting == "hard":
        _light(f"sun-{lighting}", "SUN", coll, centre, r, key_dir, up, SUN_STRENGTH)
    else:
        _light(f"key-{lighting}", "AREA", coll, centre, r, key_dir, up, key * DIM_FACTOR)


def _world(lighting):
    import bpy
    world = bpy.data.worlds.new(f"world-{lighting}")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = (*WORLD_GREY, 1.0)
    bg.inputs["Strength"].default_value = WORLD_STRENGTH[lighting]
    world.color = WORLD_GREY
    return world


def _eevee_engine():
    import bpy
    items = bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys()
    return "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"


def _cycles_device():
    """Enable the first GPU backend in GPU_BACKENDS that has a non-CPU device; returns its name, or "CPU"."""
    import bpy
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    for backend in GPU_BACKENDS:
        try:
            prefs.compute_device_type = backend
        except TypeError:  # not offered on this platform
            continue
        prefs.get_devices()
        if any(d.type == backend for d in prefs.devices):
            for d in prefs.devices:
                d.use = d.type == backend
            return backend
    prefs.compute_device_type = "NONE"
    return "CPU"


def _setup_render(sc, quality, resolution, samples, device):
    """Engine, samples, resolution, AgX and 8-bit RGB PNG output for one scene; returns the engine name."""
    r = sc.render
    if quality == "final":
        r.engine = "CYCLES"
        sc.cycles.device = "CPU" if device == "CPU" else "GPU"
        sc.cycles.samples = samples
        sc.cycles.use_denoising = True
    else:
        r.engine = _eevee_engine()
        sc.eevee.taa_render_samples = samples
        sc.eevee.use_raytracing = True
    r.resolution_x, r.resolution_y = resolution
    r.resolution_percentage = 100
    r.film_transparent = False
    r.image_settings.file_format = "PNG"
    r.image_settings.color_mode = "RGB"
    r.image_settings.color_depth = "8"
    sc.view_settings.view_transform = "AgX"
    return r.engine


def _view_layers(sc, colls):
    """One view layer per state; excluding collections is what hides the other states."""
    sc.view_layers[0].name = STATES[0]
    for state in STATES[1:]:
        sc.view_layers.new(state)
    for layer in sc.view_layers:
        shows = set(LAYER_SHOWS[layer.name]) | {"cameras"}
        if "exploded" not in colls and layer.name == "exploded":
            shows.add("assembled")  # nothing moves: the exploded state is the assembled one
        for lc in layer.layer_collection.children:
            if lc.name in colls:
                lc.exclude = lc.name not in shows


def build(scene, folder, quality, resolution, samples):
    """Build the .blend's content from a scene.json dict.

    Collections assembled, exploded (only when an offset is non-zero), tools, backdrop and cameras are linked
    into the scenes studio, hard and dim; each scene adds its own lights-<lighting> collection and World, and has
    the view layers assembled, exploded and tools. Cameras cam-<view> are framed on the assembled state (cam-tools
    on the tools state). Returns (engine, device, bbox per state, cameras, aspect).
    """
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)
    colls = {name: bpy.data.collections.new(name) for name in OBJECT_COLLECTIONS}
    objs = _build_objects(scene, _import_meshes(scene, folder), colls)
    assembled = _bbox_of(objs["assembled"]) or _bbox_of(objs["tools"])
    boxes = {
        "assembled": assembled,
        "exploded": _bbox_of(objs["exploded"]) or assembled,
        "tools": _union(assembled, _bbox_of(objs["tools"])),
    }
    _backdrop(scene, assembled, colls["backdrop"])
    cams = _cameras(colls["cameras"])
    aspect = resolution[0] / resolution[1]
    for view, cam in cams.items():
        _frame(cam, view, boxes["tools" if view == "tools" else "assembled"], scene, aspect)

    device = _cycles_device() if quality == "final" else "GPU"
    first = bpy.context.scene
    first.name = LIGHTINGS[0]
    engine = None
    for lighting in LIGHTINGS:
        sc = first if lighting == LIGHTINGS[0] else bpy.data.scenes.new(lighting)
        for coll in colls.values():
            sc.collection.children.link(coll)
        lights = bpy.data.collections.new(f"lights-{lighting}")
        sc.collection.children.link(lights)
        _lights(lighting, lights, scene, assembled)
        sc.world = _world(lighting)
        sc.camera = cams["iso"]
        _view_layers(sc, colls)
        engine = _setup_render(sc, quality, resolution, samples, device)
    return engine, device, boxes, cams, aspect


def render_stills(scene, stills, boxes, cams, aspect):
    """Render each (state, view, lighting, path), its camera reframed on that state's bbox."""
    import time
    from pathlib import Path

    import bpy
    done = []
    for state, view, lighting, path in stills:
        if state not in STATES or view not in VIEWS or lighting not in LIGHTINGS:
            raise ValueError(f"unknown still {state}:{view}:{lighting}")
        sc = bpy.data.scenes[lighting]
        _frame(cams[view], view, boxes[state], scene, aspect)
        sc.camera = cams[view]
        out = Path(path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        sc.render.filepath = str(out)
        start = time.perf_counter()
        bpy.ops.render.render(write_still=True, scene=sc.name, layer=state)
        done.append({"state": state, "view": view, "lighting": lighting, "path": str(path),
                     "seconds": round(time.perf_counter() - start, 2)})
    return done


def _default_stills(scene, folder):
    objects = scene["objects"]
    has_tools = any(o["collection"] == "tools" for o in objects)
    has_explode = any(any(o["explode"]) for o in objects if o["collection"] != "tools")
    plan = still_plan((), (), (), has_tools=has_tools, has_explode=has_explode)
    return [(*s, str(folder / still_name(*s))) for s in plan]


def _flush_c_stdio():
    """Flush Blender's C-level stdout, so its buffered banner can't land after the result line."""
    import ctypes
    import sys
    try:
        (ctypes.CDLL("ucrtbase") if sys.platform == "win32" else ctypes.CDLL(None)).fflush(None)
    except (OSError, AttributeError):
        pass


def main(argv) -> int:
    """Build and save the .blend, then render the stills. The last stdout line is `PRINT3D_RESULT <json>`.

    Without --still the default set goes next to scene.json; without --blend the .blend is scene.blend there.
    """
    import json
    import sys
    import traceback
    from pathlib import Path
    result = {"ok": False, "blender_version": None, "engine": None, "device": None, "stills": [], "blend": None,
              "error": None}
    code = 1
    try:
        import bpy
        result["blender_version"] = bpy.app.version_string
        args = parse_args(argv)
        scene_path = Path(args.scene_json).resolve()
        scene = json.loads(scene_path.read_text(encoding="utf-8"))
        folder = scene_path.parent
        q = QUALITY[args.quality]
        resolution = args.resolution or q["resolution"]
        samples = args.samples or q["samples"]
        stills = args.still or _default_stills(scene, folder)
        engine, device, boxes, cams, aspect = build(scene, folder, args.quality, resolution, samples)
        result["engine"], result["device"] = engine, device
        blend = Path(args.blend).resolve() if args.blend else folder / "scene.blend"
        blend.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        result["blend"] = str(blend)
        result["stills"] = render_stills(scene, stills, boxes, cams, aspect)
        result["ok"], code = True, 0
    except BaseException:  # noqa: BLE001 - report everything, argparse's SystemExit included
        result["error"] = traceback.format_exc()
    sys.stdout.flush()
    _flush_c_stdio()
    print("PRINT3D_RESULT " + json.dumps(result), flush=True)
    return code


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
