"""print3d assemble: load an assembly.py, build and place its parts and hardware, check every unit and layout,
and write the result (out/assembly.result.json) and the evidence sheets (out/assembly/).

build123d is imported lazily, as in run_model, so that loading and parameter errors stay fast.
"""
from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import json
import re
import sys
import time
from dataclasses import dataclass, field
from importlib.machinery import SourceFileLoader
from pathlib import Path

import print3d_shared
import run_model
from run_model import RunError


# --- loading ------------------------------------------------------------------------------------------------------


def _load_interfaces(root):
    if not (Path(root) / "interfaces.py").is_file():
        return {}
    try:
        return print3d_shared.load_interfaces(root)
    except ValueError as exc:
        raise RunError("load", str(exc), exc=exc) from exc


@contextlib.contextmanager
def _project(root, shared_overrides):
    """The project root on sys.path and active for shared(), as run_model.load_model does for a part."""
    inserted = str(root)
    sys.path.insert(0, inserted)
    try:
        with print3d_shared.activate(root, shared_overrides):
            yield
    finally:
        if inserted in sys.path:
            sys.path.remove(inserted)


def load_assembly(path, set_pairs=()):
    """Load assembly.py. Returns (module, params, shared_overrides).

    --set keys must be in interfaces.py (they become shared_overrides, seen by every part and by the assembly)
    or in the assembly's PARAMETERS. Anything else is a params error that lists both sets of valid names.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise RunError("usage", f"Assembly file not found: {path.as_posix()}")
    if path.suffix.lower() != ".py":
        raise RunError("usage", f"Assembly file must be a .py file: {path.as_posix()}")
    raw = run_model.parse_overrides(set_pairs)
    root = path.parent
    interfaces = _load_interfaces(root)
    shared_raw = {k: v for k, v in raw.items() if k in interfaces}
    shared_overrides = {}
    if shared_raw:
        _, shared_overrides = run_model.merge_parameters(interfaces, shared_raw)

    module_name = "print3d_assembly_" + re.sub(r"\W", "_", root.name)
    spec = importlib.util.spec_from_file_location(module_name, path, loader=SourceFileLoader(module_name, str(path)))
    module = importlib.util.module_from_spec(spec)
    try:
        with contextlib.redirect_stdout(sys.stderr), _project(root, shared_overrides):
            spec.loader.exec_module(module)
    except SyntaxError as exc:
        raise RunError("load", f"SyntaxError: {exc.msg}", exc=exc, line=exc.lineno) from exc
    except (Exception, SystemExit) as exc:
        raise RunError("load", f"{type(exc).__name__}: {exc}", exc=exc) from exc

    params = getattr(module, "PARAMETERS", {})
    if not isinstance(params, dict):
        raise RunError("load", "PARAMETERS in assembly.py must be a dict of name -> default value")
    for key, value in params.items():
        if not isinstance(key, str) or not isinstance(value, run_model.ALLOWED_TYPES):
            raise RunError("load", f"PARAMETERS[{key!r}] must be int, float, bool or str (got {type(value).__name__})")
    if not callable(getattr(module, "build_assembly", None)):
        raise RunError("load", "assembly.py must define a function build_assembly(a) that returns an Assembly")

    rest = {k: v for k, v in raw.items() if k not in interfaces}
    unknown = sorted(set(rest) - set(params))
    if unknown:
        raise RunError(
            "params",
            f"Unknown parameter(s): {', '.join(unknown)}. "
            f"Shared values (interfaces.py): {', '.join(interfaces) or 'none'}. "
            f"Assembly PARAMETERS: {run_model.describe_parameters(params) or 'none'}",
        )
    merged, _ = run_model.merge_parameters(params, rest)
    module.__print3d_root__ = root
    return module, merged, shared_overrides


def declare_assembly(module, params, shared_overrides):
    """Call the module's build_assembly(a) inside the project; returns the Assembly (load-stage errors)."""
    from print3d_assembly import Assembly

    root = getattr(module, "__print3d_root__", Path(module.__file__).resolve().parent)
    try:
        with contextlib.redirect_stdout(sys.stderr), _project(root, shared_overrides):
            declared = module.build_assembly(dict(params))
    except (Exception, SystemExit) as exc:
        raise RunError("load", f"build_assembly(): {type(exc).__name__}: {exc}", exc=exc) from exc
    if not isinstance(declared, Assembly):
        raise RunError(
            "load", f"build_assembly(a) must return an Assembly(units=[...]), got {type(declared).__name__}"
        )
    return declared


# --- building and placing -----------------------------------------------------------------------------------------


@dataclass
class PlacedItem:
    """One item placed in a unit (or layout) frame.

    kind: "part", "screw", "nut", "washer" or "standin". model: the resolved model path (parts only).
    shared: the shared values the part's model asked for. decl: the declaring object (Part, Screw, ...).
    loc: where the item's local frame sits, e.g. a fastener's axis frame (origin, local +Z = direction).
    """

    name: str
    kind: str
    shape: object
    color: tuple | None
    model: str | None
    shared: dict
    decl: object = None
    loc: object = None

    def axis(self):
        """(origin, direction) of the item's local frame in this frame: a fastener's axis."""
        from build123d import Plane

        frame = Plane(self.loc)
        return tuple(frame.origin), tuple(frame.z_dir)


def _check_part_params(decl, defaults):
    """Part.params (minus "_part") merged over the model's PARAMETERS, type-checked like --set values."""
    given = {k: v for k, v in decl.params.items() if k != "_part"}
    unknown = sorted(set(given) - set(defaults))
    if unknown:
        raise RunError(
            "params",
            f"Part '{decl.name}' ({decl.model}): unknown parameter(s) {', '.join(unknown)}. "
            f"Valid parameters: {run_model.describe_parameters(defaults)}",
        )
    params = dict(defaults)
    for key, value in given.items():
        default = defaults[key]
        ok = type(value) is type(default) or (
            isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool)
        )
        if not ok:
            raise RunError(
                "params",
                f"Part '{decl.name}' ({decl.model}): parameter '{key}' expects {type(default).__name__}, "
                f"got {type(value).__name__} ({value!r})",
            )
        params[key] = float(value) if isinstance(default, float) else value
    return params


class Builder:
    """Loads part models once per path and builds each (path, params) once; places items as PlacedItems."""

    def __init__(self, root, shared_overrides, result):
        self.root = Path(root)
        self.shared_overrides = dict(shared_overrides or {})
        self.result = result
        self.interfaces = _load_interfaces(self.root)
        self.modules = {}
        self.builds = {}

    def _warn(self, text):
        warnings = self.result.setdefault("warnings", [])
        if text not in warnings:
            warnings.append(text)

    def _wrap(self, err, decl, path):
        """Name the part in a RunError; the line stays the model's own (as run reports it)."""
        line = run_model.error_info(err, path)["line"]
        wrapped = RunError(err.stage, f"Part '{decl.name}' ({decl.model}): {err.message}", exc=err.exc, line=line)
        wrapped.model = path.as_posix()
        return wrapped

    def _module(self, decl, path):
        if path not in self.modules:
            if not path.is_file():
                err = RunError("load", f"Part '{decl.name}': model file not found: {path.as_posix()}")
                err.model = path.as_posix()
                raise err
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    self.modules[path] = run_model.load_model(path, self.shared_overrides)
            except RunError as err:
                raise self._wrap(err, decl, path) from err
        return self.modules[path]

    def _shared_warnings(self, decl, module):
        from_shared = getattr(module, "__print3d_shared__", {})
        for key, value in module.PARAMETERS.items():
            if key in self.interfaces and (key not in from_shared or from_shared[key] != value):
                self._warn(
                    f"Part '{decl.name}' ({decl.model}): parameter '{key}' has a shared name but does not "
                    "come from shared(); assemble --set won't change it"
                )

    def _build(self, decl, path, module, params):
        key = (path, tuple(sorted(params.items())))
        if key not in self.builds:
            try:
                with contextlib.redirect_stdout(sys.stderr), _project(self.root, self.shared_overrides):
                    try:
                        built = module.build(dict(params))
                    except (Exception, SystemExit) as exc:
                        raise RunError("build", f"{type(exc).__name__}: {exc}", exc=exc) from exc
                    self.builds[key] = run_model.normalize_parts(built)
            except RunError as err:
                raise self._wrap(err, decl, path) from err
        return self.builds[key]

    def _select(self, decl, path, shapes):
        pick = decl.params.get("_part")
        if pick is None:
            if len(shapes) > 1:
                err = RunError(
                    "build",
                    f"Part '{decl.name}' ({decl.model}): build() returned several parts ({', '.join(shapes)}); "
                    f"pick one with params={{'_part': '<name>'}}",
                )
                err.model = path.as_posix()
                raise err
            return next(iter(shapes.values()))
        if pick not in shapes:
            err = RunError(
                "build",
                f"Part '{decl.name}' ({decl.model}): _part {pick!r} is not one of the model's parts "
                f"({', '.join(shapes)})",
            )
            err.model = path.as_posix()
            raise err
        return shapes[pick]

    def item(self, decl):
        """Build and place one declared item in its own (unit or layout) frame."""
        from print3d_assembly import Part

        if isinstance(decl, Part):
            path = (self.root / decl.model).resolve()
            module = self._module(decl, path)
            self._shared_warnings(decl, module)
            params = _check_part_params(decl, module.PARAMETERS)
            shape = self._select(decl, path, self._build(decl, path, module, params))
            shared = dict(getattr(module, "__print3d_shared__", {}))
            return PlacedItem(decl.name, "part", shape.moved(decl.at), decl.color, path.as_posix(), shared, decl, decl.at)
        return place_simple(decl)


def place_simple(decl):
    """Place hardware or a stand-in; these need no project to build."""
    import hardware
    from build123d import Location, Plane
    from print3d_assembly import Nut, Screw, StandIn, Washer

    if isinstance(decl, StandIn):
        return PlacedItem(decl.name, "standin", decl.shape.moved(decl.at), decl.color, None, {}, decl, decl.at)
    if isinstance(decl, Screw):
        kind, solid = "screw", hardware.screw(decl.size, decl.kind, decl.length)
    elif isinstance(decl, Nut):
        kind, solid = "nut", hardware.nut(decl.size, decl.kind)
    elif isinstance(decl, Washer):
        kind, solid = "washer", hardware.washer(decl.size)
    else:
        raise TypeError(f"not an assembly item: {decl!r}")
    loc = Location(Plane(origin=decl.origin, z_dir=decl.direction))
    if isinstance(decl, Nut) and decl.angle:
        loc = loc * Location((0, 0, 0), (0, 0, 1), decl.angle)  # turned about its own axis, after the frame
    return PlacedItem(decl.name, kind, solid.moved(loc), decl.color, None, {}, decl, loc)


def build_items(assembly, root, shared_overrides, result, *, profile=None, builder=None):
    """Build and place every unit's items: {unit name: {item name: PlacedItem}}.

    Activates `profile` (None: the default base profile) as run does, since parts call profile helpers.
    Warnings go to result["warnings"]. Pass a Builder to share its build cache with place_layout.
    """
    run_model.activate_profile(profile)
    builder = builder or Builder(root, shared_overrides, result)
    return {unit.name: {decl.name: builder.item(decl) for decl in unit.items} for unit in assembly.units}


def _moved(placed, name, loc):
    return dataclasses.replace(placed, name=name, shape=placed.shape.moved(loc), loc=loc * placed.loc)


def place_layout(layout, unit_items, builder=None):
    """Place a layout's unit instances and items: {"<instance>.<item>" or layout item name: PlacedItem}.

    Instances whose layout.overrides give part params are rebuilt with those params; that and layout Parts
    need `builder` (the one build_items used, so identical builds are shared).
    """
    from print3d_assembly import Part

    placed = {}
    for inst, unit_name, loc in layout.instances:
        variants = layout.overrides.get(inst, {})
        for name, item in unit_items[unit_name].items():
            if name in variants:
                if builder is None:
                    raise ValueError(f"Layout {layout.name!r}: overrides for {inst!r} need a Builder to rebuild {name!r}")
                decl = item.decl
                item = builder.item(dataclasses.replace(decl, params={**decl.params, **variants[name]}))
            placed[f"{inst}.{name}"] = _moved(item, f"{inst}.{name}", loc)
    for decl in layout.items:
        if isinstance(decl, Part) and builder is None:
            raise ValueError(f"Layout {layout.name!r}: the layout part {decl.name!r} needs a Builder to build it")
        placed[decl.name] = builder.item(decl) if builder else place_simple(decl)
    return placed


# --- layout checks ------------------------------------------------------------------------------------------------


def _instance_of(name):
    """The instance of a placed layout name ("c1.block" -> "c1"); None for a layout item."""
    inst, dot, _ = name.partition(".")
    return inst if dot else None


def _sweep_pairs(placed, layout):
    """The pairs a layout's overlap sweep looks at, in placed order.

    Items of different exact instances; inside an exact instance with overrides (a variant the unit level didn't
    sweep); and every layout item against every other item, exact or not.
    """
    exact = set(layout.exact)
    names = list(placed)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ia, ib = _instance_of(a), _instance_of(b)
            if ia is None or ib is None:
                pairs.append((a, b))
            elif ia in exact and ib in exact and (ia != ib or ia in layout.overrides):
                pairs.append((a, b))
    return pairs


def _in_instance(result, inst, loc):
    """A unit-level result measured in instance `inst`'s unit frame, reported in the layout frame.

    Named "<inst>/<name>", items "<inst>.<item>"; location and planes moved by the instance Location `loc`.
    """
    from build123d import Location, Vector

    def point(p):
        return None if p is None or None in p else [round(v, 4) + 0.0 for v in (loc * Location(Vector(*p))).position]

    def normal(p, n):
        if point(p) is None or n is None or None in n:
            return n
        tip = (loc * Location(Vector(*p) + Vector(*n))).position - (loc * Location(Vector(*p))).position
        return [round(v, 6) + 0.0 for v in tip]

    moved = {**result, "name": f"{inst}/{result['name']}", "instance": inst,
             "items": [f"{inst}.{n}" for n in result["items"]]}
    if "location" in result:
        moved["location"] = point(result["location"])
    if "planes" in result:
        moved["planes"] = [(point(p), normal(p, n)) for p, n in result["planes"]]
    return moved


def _check_variants(layout, unit_items, units, builder):
    """Check each exact instance with overrides (a unit variant) against its own unit's interfaces and targets.

    The unit level checks only the unit as declared, so a variant's joints are checked here, once per instance.
    They are measured in the unit frame (where the unit's points and origins are declared), on the unit's items
    with the overridden parts rebuilt, and reported in the layout frame. Returns (interface results, target
    results, intended overlap pairs) with names "<inst>/<name>" and item names "<inst>.<item>"; measured dicts
    keep the unit's bare item names.
    """
    import assembly_checks as ac

    interfaces, targets, intended = [], [], set()
    for inst, unit_name, loc in layout.instances:
        if inst not in layout.exact or inst not in layout.overrides:
            continue
        unit, variants = units[unit_name], layout.overrides[inst]
        items = dict(unit_items[unit_name])
        for name, params in variants.items():
            decl = items[name].decl
            items[name] = builder.item(dataclasses.replace(decl, params={**decl.params, **params}))
        results = {iface.name: ac.check_interface(iface, items) for iface in unit.interfaces}
        interfaces += [_in_instance(r, inst, loc) for r in results.values()]
        targets += [_in_instance(ac.check_target(t, items), inst, loc) for t in unit.targets]
        intended |= {frozenset(f"{inst}.{n}" for n in pair) for pair in ac.intended_overlaps(unit.interfaces, results)}
    return interfaces, targets, intended


def check_layout(layout, unit_items, builder=None, *, units) -> dict:
    """Check one layout: what happens between its unit instances. Returns a JSON-ready dict.

    name, instances (the count), exact (the instance names checked with full solids), interfaces, overlaps,
    targets, skipped. Every interface and target result has an `instance` key: None for the layout's own.
    - Layout interfaces are checked when every item they name belongs to an exact instance, or when they name a
      layout item (a layout item is always checked exactly against the parts it names, exact or not). The others
      go to `skipped` with a reason; they don't fail, but aren't passed either.
    - Variants: an exact instance with overrides also gets its unit's own interfaces and targets checked (as
      "<inst>/<name>"), since the unit level checks only the unit as declared. An instance with overrides outside
      the exact subset is listed in `skipped`.
    - Overlap sweep: between items of different exact instances, inside exact instances with overrides, and
      each layout item against every other item (see _sweep_pairs). Pairs inside an unchanged instance are the
      unit level's.
    - Layout targets: evaluated on every instance (distances and probes are cheap).
    units: {name: Unit} or a list of Units, the assembly's (required: a variant's checks come from its unit).
    """
    import assembly_checks as ac

    if not isinstance(units, dict):
        units = {u.name: u for u in units}
    placed = place_layout(layout, unit_items, builder)
    layout_items = {decl.name for decl in layout.items}
    exact = set(layout.exact)

    results, skipped = {}, []
    for iface in layout.interfaces:
        names = [name for _, name, _ in iface.references()]
        outside = [n for n in names if n not in layout_items and _instance_of(n) not in exact]
        if outside and not any(n in layout_items for n in names):
            missing = sorted({_instance_of(n) for n in outside})
            skipped.append({
                "name": iface.name,
                "type": ac._type_name(iface),
                "items": names,
                "reason": f"{', '.join(outside)} not in the exact subset ({', '.join(layout.exact) or 'none'}); "
                          f"add {', '.join(missing)} to exact to check it",
            })
            continue
        results[iface.name] = {**ac.check_interface(iface, placed), "instance": None}

    for inst in layout.overrides:
        if inst not in exact:
            skipped.append({
                "name": inst,
                "type": "instance",
                "items": sorted(n for n in placed if _instance_of(n) == inst),
                "reason": f"{inst} has overrides (a variant of unit {layout.unit_of(inst)!r}) but isn't in the exact "
                          "subset: the variant's own interfaces, targets and overlaps, and its overlaps with "
                          "neighbours, aren't checked; add it to exact to check them",
            })

    variant_ifaces, variant_targets, variant_intended = _check_variants(layout, unit_items, units, builder)
    intended = ac.intended_overlaps(layout.interfaces, results) | variant_intended
    return {
        "name": layout.name,
        "instances": len(layout.instances),
        "exact": list(layout.exact),
        "interfaces": list(results.values()) + variant_ifaces,
        "overlaps": ac.overlap_sweep(placed, intended, _sweep_pairs(placed, layout)),
        "targets": [{**ac.check_target(t, placed), "instance": None} for t in layout.targets] + variant_targets,
        "skipped": skipped,
    }


# --- the assemble command -----------------------------------------------------------------------------------------

LEVELS = ("unit", "layout", "all")
SHEET_DIR = "assembly"  # sheets go in out/assembly/<unit or layout>/<check>.png
RESULT_FILE = "assembly.result.json"
SHEET_MANIFEST = ".print3d-sheets.json"  # in out/assembly/: the sheets the last run wrote (only these are cleared)


def _select(kind, declared, wanted):
    """The declared objects to check: all of them, or the one named `wanted` (a usage error if unknown)."""
    if wanted is None:
        return list(declared.values())
    if wanted not in declared:
        raise RunError("usage", f"Unknown {kind} {wanted!r}; {kind}s: {', '.join(declared) or 'none'}")
    return [declared[wanted]]


def _levels(level, unit, layout):
    """(check units, check layouts) for --level and --unit / --layout."""
    if level not in LEVELS:
        raise RunError("usage", f"Unknown level {level!r}; choose from {', '.join(LEVELS)}")
    if unit is not None and level == "layout":
        raise RunError("usage", "--unit cannot be combined with --level layout")
    if layout is not None and level == "unit":
        raise RunError("usage", "--layout cannot be combined with --level unit")
    units = level in ("unit", "all") and not (layout is not None and unit is None)
    layouts = level in ("layout", "all") and not (unit is not None and layout is None)
    return units, layouts


def check_unit(unit, items) -> dict:
    """Check one unit: its interfaces, then the overlap sweep (the snap results decide intended pairs), then targets."""
    import assembly_checks as ac

    results = {iface.name: ac.check_interface(iface, items) for iface in unit.interfaces}
    intended = ac.intended_overlaps(unit.interfaces, results)
    return {
        "name": unit.name,
        "parts": [{"name": n, "model": i.model, "shared": dict(i.shared)}
                  for n, i in items.items() if i.kind == "part"],
        "interfaces": list(results.values()),
        "overlaps": ac.overlap_sweep(items, intended),
        "targets": [ac.check_target(t, items) for t in unit.targets],
        "sheets": {},
    }


def _entries(group):
    return group["interfaces"] + group["overlaps"] + group["targets"]


def _passed(results):
    return sum(r["status"] == "pass" for r in results), sum(r["status"] == "fail" for r in results)


def _skipped_interfaces(groups):
    """Skipped interfaces only: a non-exact variant instance (type "instance") is listed in skipped, not counted."""
    return sum(entry.get("type") != "instance" for g in groups for entry in g.get("skipped", []))


def _summary(groups):
    ip, if_ = _passed([r for g in groups for r in g["interfaces"]])
    tp, tf = _passed([r for g in groups for r in g["targets"]])
    return {
        "interfaces": {"pass": ip, "fail": if_, "skipped": _skipped_interfaces(groups)},
        "overlaps": sum(len(g["overlaps"]) for g in groups),
        "targets": {"pass": tp, "fail": tf},
    }


def _slug(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "check"


class _Stems:
    """Unique file stems within one folder (so an interface named "overview" can't overwrite the overview)."""

    def __init__(self, reserved=()):
        self.used = set(reserved)

    def __call__(self, name):
        stem, k = _slug(name), 2
        while stem in self.used:
            stem, k = f"{_slug(name)}-{k}", k + 1
        self.used.add(stem)
        return stem


def _check_name(entry):
    return entry.get("name") or f"overlap {entry['a']} / {entry['b']}"


def _overview_lines(group, kind):
    """Info lines for an overview sheet, and the failing entries (numbered markers on the views)."""
    failed = [r for r in _entries(group) if r["status"] == "fail"]
    ip, if_ = _passed(group["interfaces"])
    tp, tf = _passed(group["targets"])
    skipped = f", {len(group['skipped'])} skipped" if group.get("skipped") else ""
    lines = [f"Status: {'fail' if failed else 'pass'}", f"Interfaces: {ip} pass, {if_} fail{skipped}",
             f"Overlaps: {len(group['overlaps'])}", f"Targets: {tp} pass, {tf} fail"]
    if kind == "layout":
        lines.append(f"Instances: {group['instances']}; exact: {', '.join(group['exact']) or 'none'}")
    if failed:
        lines.append("Failures (numbered on the views):")
        lines += [f" {i + 1} {_check_name(r)}: {r['message']}" for i, r in enumerate(failed)]
    return lines, failed


def _group_sheets(group, kind, items, folder, title, highlight, result):
    """Write one unit's or layout's sheets; each failure becomes a warning, never an error."""
    import assembly_sheets

    label = f"{kind} {group['name']!r}"
    try:
        with contextlib.redirect_stdout(sys.stderr):
            scene = assembly_sheets.make_scene(items)
    except Exception as exc:
        result["warnings"].append(f"Sheets skipped for {label}: {type(exc).__name__}: {exc}")
        return
    stems = _Stems({"overview"})
    results = _entries(group)
    faults, engaged = assembly_sheets.fault_pairs(results), assembly_sheets.engaged_pairs(results)
    for entry in group["interfaces"] + group["overlaps"]:
        if not entry.get("planes"):
            continue
        check = _check_name(entry)
        names = set(entry.get("items") or (entry["a"], entry["b"]))
        overlaps = [o for o in group["overlaps"] if {o["a"], o["b"]} <= names]  # an overlap entry: itself
        try:
            with contextlib.redirect_stdout(sys.stderr):
                path = assembly_sheets.interface_sheet(f"{group['name']} / {check}", scene, entry,
                                                       folder / f"{stems(check)}.png", faults=faults,
                                                       engaged=engaged, overlaps=overlaps)
            entry["evidence"].append(path)
        except Exception as exc:
            result["warnings"].append(f"Sheet failed for {label}, check {check!r}: {type(exc).__name__}: {exc}")
    lines, failed = _overview_lines(group, kind)
    markers = [(tuple(r["location"]), i + 1) for i, r in enumerate(failed)
               if r.get("location") and None not in r["location"]]
    try:
        with contextlib.redirect_stdout(sys.stderr):
            group["sheets"]["overview"] = assembly_sheets.overview_sheet(
                title, scene, folder / "overview.png", highlight, lines=lines, markers=markers)
    except Exception as exc:
        result["warnings"].append(f"Overview sheet failed for {label}: {type(exc).__name__}: {exc}")


def _inside(path, root):
    """True when `path` (resolved: symlinks and junctions followed) lies inside the resolved `root`."""
    try:
        Path(path).resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    return True


def _prune_empty(folder, root):
    """Remove the empty folders under `folder` (and it), bottom up; never descends through a link or junction."""
    for child in sorted(folder.iterdir()):
        real = child.resolve()
        if child.is_dir() and real == root / child.relative_to(root) and _inside(real, root):
            _prune_empty(child, root)
    if not any(folder.iterdir()):
        folder.rmdir()


def clear_sheets(out, result):
    """Remove the sheets an earlier run wrote, so none sits next to this result. Never deletes anything else.

    Only the files listed in out/assembly/.print3d-sheets.json (written by stage_sheets) go, if they resolve to a
    PNG inside out/assembly/; then the manifest and the folders left empty. Without a manifest nothing is deleted
    and a warning says the sheets there may be stale. Any failure is a warning.
    """
    folder = out / SHEET_DIR
    if not folder.is_dir():
        return
    root = folder.resolve()
    manifest = root / SHEET_MANIFEST
    stale = f"{folder.as_posix()} has files print3d didn't list as its own sheets; any sheets there may be stale"
    if not manifest.is_file():
        result["warnings"].append(f"Not cleared: {stale}")
        return
    try:
        listed = json.loads(manifest.read_text(encoding="utf-8"))["files"]
        for rel in listed:
            path = root / rel
            if (isinstance(rel, str) and path.suffix.lower() == ".png" and _inside(path, root)
                    and path.is_file() and not path.is_symlink()):
                path.unlink()
        manifest.unlink()
        _prune_empty(root, root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["warnings"].append(f"Could not clear the old sheets ({type(exc).__name__}: {exc}): {stale}")


def _write_manifest(result, out):
    """List every sheet this run wrote, relative to out/assembly/, so the next run can remove exactly those."""
    root = (out / SHEET_DIR).resolve()
    paths = [p for g in result["units"] + result["layouts"] for e in g["interfaces"] + g["overlaps"]
             for p in e.get("evidence", [])] + [p for g in result["units"] + result["layouts"]
                                                 for p in g["sheets"].values()]
    files = sorted({Path(p).resolve().relative_to(root).as_posix() for p in paths if _inside(p, root)})
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / SHEET_MANIFEST).write_text(json.dumps({"files": files}, indent=2), encoding="utf-8")
    except OSError as exc:
        result["warnings"].append(f"Could not write the sheet list {SHEET_MANIFEST}: {exc}")


def stage_sheets(result, unit_items, layout_items, layouts, out):
    """Render every evidence sheet into out/assembly/; as run's preview, a failure is only a warning."""
    try:
        import assembly_sheets  # noqa: F401
    except ImportError as exc:  # e.g. pillow missing because setup.ps1 wasn't re-run after an update
        result["warnings"].append(
            f"Sheets skipped: the Python package {exc.name or str(exc)!r} is not installed. "
            "Re-run skills/print3d/scripts/setup.ps1 to update the print3d environment."
        )
        return
    folders = _Stems()
    root = out / SHEET_DIR
    for group in result["units"]:
        _group_sheets(group, "unit", unit_items[group["name"]], root / folders(group["name"]),
                      f"Unit {group['name']}", (), result)
    for group in result["layouts"]:
        layout = layouts[group["name"]]
        exact = ", ".join(layout.exact) or "none"
        highlight = tuple(layout.exact) + tuple(decl.name for decl in layout.items)
        _group_sheets(group, "layout", layout_items[group["name"]], root / folders(group["name"]),
                      f"Layout {group['name']}  ·  {group['instances']} instances, exact: {exact}", highlight, result)
    _write_manifest(result, out)


def _exit_code(result, strict):
    if not result["ok"]:
        return 2 if result["error"]["stage"] == "usage" else 1
    return 3 if strict and result["status"] == "fail" else 0


def _timed(result, name, fn, *args):
    start = time.perf_counter()
    value = fn(*args)
    result["timings_s"][name] = run_model._r3(time.perf_counter() - start)
    return value


def _stage_load(path, set_pairs, result):
    module, params, shared_overrides = load_assembly(path, set_pairs)
    assembly = declare_assembly(module, params, shared_overrides)
    raw = run_model.parse_overrides(set_pairs)
    result["parameters"] = dict(params)
    result["overrides"] = {**shared_overrides, **{k: params[k] for k in raw if k not in shared_overrides}}
    result["warnings"] += [note for note in assembly.notes if note not in result["warnings"]]
    return assembly, shared_overrides


def _stage_build(assembly, builder, unit_list, layout_list, profile):
    """Build the items of every unit that is checked or used by a checked layout, and place the layouts."""
    needed = {u.name for u in unit_list} | {name for lay in layout_list for _, name, _ in lay.instances}
    run_model.activate_profile(profile)
    unit_items = {u.name: {decl.name: builder.item(decl) for decl in u.items}
                  for u in assembly.units if u.name in needed}
    layout_items = {lay.name: place_layout(lay, unit_items, builder) for lay in layout_list}
    return unit_items, layout_items


def _stage_check(assembly, builder, unit_items, unit_list, layout_list, result):
    result["units"] = [check_unit(u, unit_items[u.name]) for u in unit_list]
    result["layouts"] = [{**check_layout(lay, unit_items, builder, units=assembly.units), "sheets": {}}
                         for lay in layout_list]
    groups = result["units"] + result["layouts"]
    for group in groups:
        for entry in group["interfaces"] + group["overlaps"]:
            entry.setdefault("evidence", [])
    result["summary"] = _summary(groups)
    result["status"] = "fail" if any(r["status"] == "fail" for g in groups for r in _entries(g)) else "pass"


def assemble(path, set_pairs=(), level="all", unit=None, layout=None, out_dir=None, profile=None, strict=False,
             sheets=True):
    """Load, build, place and check an assembly; write out/assembly.result.json and the sheets. Returns (result, code).

    level: "unit", "layout" or "all"; unit / layout: check only that one (--unit alone checks no layout, --layout
    alone no unit; the units a layout uses are built either way). `profile` is a print3d_profile.load_profile()
    dict (None: the default base profile). out_dir: default `out` next to assembly.py; sheets go in
    out/assembly/<unit or layout>/<check>.png. Before building, the sheets the previous run listed in
    out/assembly/.print3d-sheets.json are removed (also with sheets=False, and when the run then fails), so none
    sits next to a newer result; nothing else is ever deleted (see clear_sheets). Exit codes: 0 ok (also with failing checks), 1 error, 2 usage,
    3 failing checks with strict.
    """
    path = Path(path).resolve()
    result = {
        "ok": False,
        "model": path.as_posix(),
        "parameters": {},
        "overrides": {},
        "shared": {},
        "profile": None,
        "status": None,
        "summary": None,
        "units": [],
        "layouts": [],
        "warnings": [],
        "timings_s": {},
        "error": None,
    }
    out = Path(out_dir).resolve() if out_dir else path.parent / "out"
    stage, error_model = "usage", path
    try:
        check_units, check_layouts = _levels(level, unit, layout)
        run_model.parse_overrides(set_pairs)
        profile = run_model.activate_profile(profile)

        stage = "load"
        assembly, shared_overrides = _timed(result, "load", _stage_load, path, set_pairs, result)
        stage = "usage"
        unit_list = _select("unit", {u.name: u for u in assembly.units}, unit) if check_units else []
        layout_list = _select("layout", {lay.name: lay for lay in assembly.layouts}, layout) if check_layouts else []

        clear_sheets(out, result)  # first, and also with --no-sheets: no stale evidence next to this result
        stage = "build"
        builder = Builder(path.parent, shared_overrides, result)
        result["shared"] = {**builder.interfaces, **shared_overrides}
        try:
            unit_items, layout_items = _timed(result, "build", _stage_build, assembly, builder, unit_list,
                                              layout_list, profile)
        except RunError as err:
            error_model = Path(getattr(err, "model", path))  # a part's error: its traceback frames are the part's
            raise

        stage = "check"
        _timed(result, "check", _stage_check, assembly, builder, unit_items, unit_list, layout_list, result)

        if sheets:
            stage = "sheets"
            layouts = {lay.name: lay for lay in layout_list}
            _timed(result, "sheets", stage_sheets, result, unit_items, layout_items, layouts, out)
        result["ok"] = True
    except RunError as err:
        result["error"] = run_model.error_info(err, error_model)
    except Exception as exc:
        result["error"] = run_model.internal_error(stage, exc)

    if stage != "usage" and profile is not None:
        result["profile"] = run_model.profile_record(profile)
    if not result["ok"]:
        result["status"] = None

    code = _exit_code(result, strict)
    if code != 2:
        try:
            out.mkdir(parents=True, exist_ok=True)
            (out / RESULT_FILE).write_text(json.dumps(result, indent=2), encoding="utf-8")
        except OSError as exc:
            result["warnings"].append(f"Could not write {RESULT_FILE}: {exc}")
    return result, code
