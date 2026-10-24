"""Render scenes for print3d render: a model, a unit or a layout described as a SceneGroup (local meshes, placed
objects with looks and explode offsets, tool stand-ins), and the staging folder Blender reads (STLs + scene.json).

Placement comes from the same loaders and Builder as `assemble`, so a scene never differs from what it checks.
"""
from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import assemble_model
import run_model
from run_model import RunError

RENDER_LINEAR_TOL = 0.05  # mm, render mesh tessellation (the print STLs keep run_model.LINEAR_TOL)
RENDER_ANGULAR_TOL = 0.2  # rad
EXPLODE_GAP = 10.0  # mm: the least step between exploded layers, and the hardware pull
TOOL_RGB = (60, 60, 70)  # a driver that reaches its screw
SCENE_VERSION = 1
HARDWARE_KINDS = ("screw", "nut", "washer")
MODEL_RENDER_KEYS = ("color", "material", "glow", "backdrop", "facing")
UP = (0.0, 0.0, 1.0)


@dataclass(frozen=True)
class Look:
    material: str
    rgb: tuple
    glow: float | None


@dataclass
class SceneObject:
    """One placed mesh. matrix: 4x4 row-major, mm, the mesh's local frame in the group frame."""

    name: str
    mesh: str
    matrix: tuple
    look: Look
    kind: str
    explode: tuple = (0.0, 0.0, 0.0)
    collection: str = "items"


@dataclass
class SceneGroup:
    kind: str
    name: str
    folder: str
    meshes: dict
    objects: list
    bbox: tuple
    render: object
    tool_access: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def up(self):
        return up_of(self.render)


# --- helpers ------------------------------------------------------------------------------------------------------


def up_of(render):
    """The group's up axis: the facing of a wall, +Z on a floor."""
    return tuple(render.facing) if render.backdrop == "wall" else UP


def _vadd(a, b, k=1.0):
    return tuple(float(x) + k * float(y) for x, y in zip(a, b))


def _dot(a, b):
    return sum(float(x) * float(y) for x, y in zip(a, b))


def matrix(loc):
    """A build123d Location as a 4x4 row-major tuple (mm)."""
    trsf = loc.wrapped.Transformation()
    rows = tuple(tuple(float(trsf.Value(r, c)) + 0.0 for c in range(1, 5)) for r in range(1, 4))
    return rows + ((0.0, 0.0, 0.0, 1.0),)


def _bbox(shapes):
    """((min xyz), (max xyz)) around the shapes."""
    lo, hi = [float("inf")] * 3, [float("-inf")] * 3
    for shape in shapes:
        box = shape.bounding_box()
        bmin, bmax = tuple(box.min), tuple(box.max)
        for i in range(3):
            lo[i], hi[i] = min(lo[i], bmin[i]), max(hi[i], bmax[i])
    return tuple(lo), tuple(hi)


def _box_distance(point, box):
    """Distance from a point to an axis-aligned build123d BoundBox (0 inside)."""
    bmin, bmax = tuple(box.min), tuple(box.max)
    gaps = [max(bmin[i] - point[i], 0.0, point[i] - bmax[i]) for i in range(3)]
    return sum(g * g for g in gaps) ** 0.5


def local_shape(item):
    """The item's mesh in its own local frame (the frame item.loc places)."""
    import hardware

    decl = item.decl
    if item.kind == "screw":
        return hardware.screw(decl.size, decl.kind, decl.length)
    if item.kind == "nut":
        return hardware.nut(decl.size, decl.kind)
    if item.kind == "washer":
        return hardware.washer(decl.size)
    if item.kind == "standin":
        return decl.shape
    return item.shape.moved(item.loc.inverse())  # a part: the built shape, before its placement


def tool_mesh_id(decl):
    return f"tool/{decl.kind}/{decl.size}/{decl.length:g}"


# --- explode and looks --------------------------------------------------------------------------------------------


def explode_step(bbox, up=UP):
    """The spacing between exploded layers: half the group's size along `up` (z by default), at least EXPLODE_GAP."""
    size = abs(_dot([hi - lo for lo, hi in zip(*bbox)], up))
    return max(0.5 * size, EXPLODE_GAP)


def default_explode(items, up, step) -> dict:
    """{name: offset (mm)} for every item: the declared `explode`, else the default rule.

    Parts and stand-ins rank by their bbox centre along `up` (ties by name); rank r moves r * step along up.
    Hardware takes the offset of the nearest non-hardware item (its origin to that item's bbox), plus its own
    pull along its axis: a screw +(length + EXPLODE_GAP), a washer +EXPLODE_GAP / 2, a nut -EXPLODE_GAP.
    """
    up = tuple(float(v) for v in up)
    declared = {n: tuple(i.decl.explode) for n, i in items.items() if getattr(i.decl, "explode", None) is not None}
    bodies = {n: i for n, i in items.items() if i.kind not in HARDWARE_KINDS}
    boxes = {n: i.shape.bounding_box() for n, i in bodies.items()}
    ranked = sorted(bodies, key=lambda n: (round(_dot(boxes[n].center(), up), 6), n))
    rank_of, offsets = {n: r for r, n in enumerate(ranked)}, {}
    for rank, name in enumerate(ranked):
        offsets[name] = declared.get(name, tuple(rank * step * v for v in up))
    for name, item in items.items():
        if item.kind not in HARDWARE_KINDS:
            continue
        if name in declared:
            offsets[name] = declared[name]
            continue
        origin, direction = item.axis()
        nearest = min(ranked, key=lambda n: (_box_distance(origin, boxes[n]), rank_of[n]), default=None)
        base = offsets[nearest] if nearest is not None else (0.0, 0.0, 0.0)
        if item.kind == "screw":
            pull = item.decl.length + EXPLODE_GAP
        elif item.kind == "washer":
            pull = EXPLODE_GAP / 2
        else:
            pull = -EXPLODE_GAP
        offsets[name] = _vadd(base, direction, pull)
    return {name: tuple(float(v) + 0.0 for v in offsets[name]) for name in items}


def resolve_look(item, rgb, override, *, where=None) -> Look:
    """The item's look: its declared material (default metal for hardware, matte otherwise) and glow (default
    DEFAULT_GLOW when emissive), with the `override` keys (a Layout.looks entry) winning.

    An override `glow` on a look that doesn't end up emissive is a load error, labelled `where`.
    """
    from print3d_assembly import DEFAULT_GLOW

    override = override or {}
    decl = item.decl
    material = override.get("material") or getattr(decl, "material", None) or (
        "metal" if item.kind in HARDWARE_KINDS else "matte")
    rgb = tuple(override.get("color") or rgb)
    if material != "emissive":
        if override.get("glow") is not None:
            raise RunError("load", f"{where or f'item {item.name!r}'}: glow is only for material 'emissive', "
                                   f"but the look's material is {material!r}")
        return Look(material, rgb, None)
    glow = override.get("glow")
    if glow is None and getattr(decl, "material", None) == "emissive":
        glow = decl.glow
    return Look(material, rgb, float(glow if glow is not None else DEFAULT_GLOW))


# --- groups -------------------------------------------------------------------------------------------------------


def _tool_objects(items, screws, offsets, meshes, label):
    """Tool objects for the named screws (checked against every item in `items`), the access list and warnings."""
    import render_tools
    from assembly_sheets import FAIL_RGB

    tools = render_tools.place_tools(items, screws)
    access = render_tools.tool_access(items, tools)
    status = {entry["screw"]: entry for entry in access}
    objects, warnings = [], []
    for name, tool in tools.items():
        decl, entry = tool.decl, status[tool.decl.name]
        mesh = tool_mesh_id(decl)
        if mesh not in meshes:
            meshes[mesh] = render_tools.tool_solid(decl.size, decl.kind, decl.length)
        blocked = entry["status"] == "blocked"
        objects.append(SceneObject(name, mesh, matrix(tool.loc), Look("matte", FAIL_RGB if blocked else TOOL_RGB, None),
                                   "tool", offsets.get(decl.name, (0.0, 0.0, 0.0)), "tools"))
        if blocked:
            warnings.append(f"{label}: the driver for screw {decl.name!r} is blocked by {', '.join(entry['hits'])}")
    return objects, access, warnings


def unit_group(unit, unit_items) -> SceneGroup:
    """One unit's scene: its items with their looks and explode offsets, and a tool on each screw."""
    from assembly_sheets import item_colors

    render = unit.render or _default_render()
    colours = item_colors(unit_items)
    bbox = _bbox(i.shape for i in unit_items.values())
    up = up_of(render)
    offsets = default_explode(unit_items, up, explode_step(bbox, up))
    meshes, objects = {}, []
    for name, item in unit_items.items():
        mesh = f"unit/{unit.name}/{name}"
        meshes[mesh] = local_shape(item)
        objects.append(SceneObject(name, mesh, matrix(item.loc), resolve_look(item, colours[name], None),
                                   item.kind, offsets[name]))
    screws = [n for n, i in unit_items.items() if i.kind == "screw"]
    tools, access, warnings = _tool_objects(unit_items, screws, offsets, meshes, f"unit {unit.name!r}")
    return SceneGroup("unit", unit.name, f"unit-{assemble_model._slug(unit.name)}", meshes, objects + tools, bbox,
                      render, access, warnings)


def layout_group(layout, unit_items, units, builder, *, placed=None) -> SceneGroup:
    """One layout's scene: every instance's items (unit meshes reused, variants their own), the layout items
    exploded among themselves, and tools on the layout-level screws only (checked against everything placed).

    placed: the layout's place_layout dict when the caller has it already (load_groups passes assemble's own).
    """
    from assembly_sheets import item_colors
    from print3d_assembly import Screw

    render = layout.render or _default_render()
    if placed is None:
        placed = assemble_model.place_layout(layout, unit_items, builder)
    bbox = _bbox(i.shape for i in placed.values())
    up = up_of(render)
    meshes, objects = {}, []
    for inst, unit_name, _ in layout.instances:
        colours = item_colors(unit_items[unit_name])
        variants, looks = layout.overrides.get(inst, {}), layout.looks.get(inst, {})
        for name, unit_item in unit_items[unit_name].items():
            item = placed[f"{inst}.{name}"]
            if name in variants:
                mesh = f"layout/{layout.name}/{inst}/{name}"
                meshes[mesh] = local_shape(item)
            else:
                mesh = f"unit/{unit_name}/{name}"
                if mesh not in meshes:
                    meshes[mesh] = local_shape(unit_item)
            where = f"Layout {layout.name!r}: looks[{inst!r}][{name!r}]"
            look = resolve_look(item, colours[name], looks.get(name), where=where)
            objects.append(SceneObject(item.name, mesh, matrix(item.loc), look, item.kind))
    own = {decl.name: placed[decl.name] for decl in layout.items}
    colours = item_colors(own)
    offsets = default_explode(own, up, explode_step(bbox, up))
    for name, item in own.items():
        mesh = f"layout/{layout.name}/{name}"
        meshes[mesh] = local_shape(item)
        objects.append(SceneObject(name, mesh, matrix(item.loc), resolve_look(item, colours[name], None),
                                   item.kind, offsets[name]))
    screws = [decl.name for decl in layout.items if isinstance(decl, Screw)]
    tools, access, warnings = _tool_objects(placed, screws, offsets, meshes, f"layout {layout.name!r}")
    return SceneGroup("layout", layout.name, f"layout-{assemble_model._slug(layout.name)}", meshes, objects + tools,
                      bbox, render, access, warnings)


def _default_render():
    from print3d_assembly import Render

    return Render()


def _model_render(module):
    """The model's RENDER dict as (Render, look values), validated like a Render and an item look."""
    from print3d_assembly import Render, _LookProbe

    spec = getattr(module, "RENDER", None)
    if spec is None:
        return Render(), {}
    if not isinstance(spec, dict):
        raise RunError("load", f"RENDER must be a dict with keys {', '.join(MODEL_RENDER_KEYS)}; got {spec!r}")
    unknown = [k for k in spec if k not in MODEL_RENDER_KEYS]
    if unknown:
        raise RunError("load", f"RENDER: unknown key(s) {', '.join(map(repr, unknown))}; "
                               f"valid: {', '.join(MODEL_RENDER_KEYS)}")
    try:
        probe = _LookProbe("RENDER", spec.get("color"), spec.get("material"), spec.get("glow"))
        render = Render(spec.get("backdrop", "floor"), spec.get("facing"))
    except ValueError as exc:
        raise RunError("load", f"RENDER: {exc}", exc=exc) from exc
    return render, {"color": probe.color, "material": probe.material, "glow": probe.glow}


def _load_model_group(path, set_pairs):
    from assembly_sheets import PALETTE
    from print3d_assembly import DEFAULT_GLOW

    path = Path(path).resolve()
    result = {"parts": [], "warnings": []}
    module = run_model.stage_load(path)
    params = run_model.stage_params(module, run_model.parse_overrides(set_pairs), result)
    render, look = _model_render(module)
    shared = run_model.used_shared(dict(getattr(module, "__print3d_shared__", {})), params)
    result["warnings"] += run_model.shared_param_warnings(
        module, run_model._project_interfaces(path), f"Part '{path.stem}' ({path.name})")
    parts = run_model.stage_build(module, params, result)
    meshes, objects = {}, []
    for k, (name, shape) in enumerate(parts.items()):
        mesh = f"model/{name}"
        meshes[mesh] = shape
        material = look.get("material") or "matte"
        glow = (look.get("glow") or DEFAULT_GLOW) if material == "emissive" else None
        rgb = tuple(look.get("color") or PALETTE[k % len(PALETTE)])
        objects.append(SceneObject(name, mesh, matrix(_identity()), Look(material, rgb, glow), "part"))
    group = SceneGroup("model", path.stem, assemble_model._slug(path.stem), meshes, objects,
                       _bbox(parts.values()), render, [], [])
    meta = {"parameters": dict(params), "overrides": dict(result["overrides"]), "shared": shared,
            "warnings": list(result["warnings"])}
    return group, meta


def _identity():
    from build123d import Location

    return Location()


def model_group(path, set_pairs=()) -> SceneGroup:
    """A model.py's scene: each part it builds, in place, coloured by RENDER or PALETTE in part order."""
    return _load_model_group(path, set_pairs)[0]


def _assembly_groups(path, set_pairs, level, unit, layout, profile):
    """assemble's own load and build stages (same warnings, same placements), then one group per selection."""
    check_units, check_layouts = assemble_model._levels(level, unit, layout)
    result = {"parameters": {}, "overrides": {}, "warnings": []}
    assembly, shared_overrides = assemble_model._stage_load(path, set_pairs, result)
    units = {u.name: u for u in assembly.units}
    unit_list = assemble_model._select("unit", units, unit) if check_units else []
    layout_list = (assemble_model._select("layout", {lay.name: lay for lay in assembly.layouts}, layout)
                   if check_layouts else [])
    builder = assemble_model.Builder(Path(path).parent, shared_overrides, result)
    unit_items, layout_items = assemble_model._stage_build(assembly, builder, unit_list, layout_list, profile)
    groups = [unit_group(u, unit_items[u.name]) for u in unit_list]
    groups += [layout_group(lay, unit_items, units, builder, placed=layout_items[lay.name]) for lay in layout_list]
    meta = {
        "parameters": result["parameters"],
        "overrides": result["overrides"],
        "shared": {**builder.interfaces, **shared_overrides},
        "warnings": result["warnings"],
    }
    return groups, meta


def load_groups(path, set_pairs=(), level="all", unit=None, layout=None, profile=None):
    """The scene groups to render and the run's meta (parameters, overrides, shared, warnings, as assemble has).

    A file named assembly.py (any case) is an assembly: one group per selected unit, then per selected layout
    (--level / --unit / --layout as in assemble). Any other .py is a model: one group.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise RunError("usage", f"File not found: {path.as_posix()}")
    if path.suffix.lower() != ".py":
        raise RunError("usage", f"Expected a .py file: {path.as_posix()}")
    run_model.parse_overrides(set_pairs)
    profile = run_model.activate_profile(profile)  # first, as assemble and run do: before any project code loads
    if path.name.lower() == "assembly.py":
        return _assembly_groups(path, set_pairs, level, unit, layout, profile)
    group, meta = _load_model_group(path, set_pairs)
    return [group], meta


# --- staging ------------------------------------------------------------------------------------------------------


def write_staging(group: SceneGroup, folder) -> Path:
    """Write meshes/<n>.stl (one per mesh, local frame) and scene.json (version 1) into `folder`; returns its path.

    Old STLs in folder/meshes go first, so none from an earlier run is left next to this scene.
    """
    from build123d import export_stl

    folder = Path(folder)
    mesh_dir = folder / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    for old in mesh_dir.glob("*.stl"):
        old.unlink()
    files = {}
    for n, (mesh, shape) in enumerate(group.meshes.items()):
        rel = f"meshes/{n}.stl"
        with contextlib.redirect_stdout(sys.stderr):
            export_stl(shape, str(folder / rel), tolerance=RENDER_LINEAR_TOL, angular_tolerance=RENDER_ANGULAR_TOL)
        files[mesh] = rel
    render = group.render
    scene = {
        "version": SCENE_VERSION,
        "group": {"kind": group.kind, "name": group.name},
        "units": "mm",
        "bbox": [list(group.bbox[0]), list(group.bbox[1])],
        "backdrop": render.backdrop,
        "facing": None if render.facing is None else list(render.facing),
        "up": list(group.up),
        "meshes": files,
        "objects": [
            {"name": o.name, "mesh": o.mesh, "matrix": [list(row) for row in o.matrix], "kind": o.kind,
             "material": o.look.material, "rgb": list(o.look.rgb), "glow": o.look.glow,
             "explode": list(o.explode), "collection": o.collection}
            for o in group.objects
        ],
        "tool_access": group.tool_access,
    }
    path = folder / "scene.json"
    path.write_text(json.dumps(scene, indent=2), encoding="utf-8")
    return path
