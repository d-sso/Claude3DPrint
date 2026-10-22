"""print3d assemble: load an assembly.py, build and place its parts and hardware (the checks come on top of this).

build123d is imported lazily, as in run_model, so that loading and parameter errors stay fast.
"""
from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import re
import sys
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
