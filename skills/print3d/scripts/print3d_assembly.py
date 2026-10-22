"""Declaration API for assembly.py: placed parts, hardware, stand-ins, interfaces, targets, units and layouts.

An assembly.py defines PARAMETERS (optional) and build_assembly(a) -> Assembly, built from these classes.
Every field is checked when an object is made, so a typo fails at the line that declares it.

Conventions:
- Positions and directions are (x, y, z) in mm, in the frame of the unit (or of the layout for layout items).
- Items are referred to by name. In a layout, "<instance>.<item>" names an item of a unit instance, and a bare
  name names a layout item.
- Required fields can be given by position. `source` and every optional field are keyword-only.
- Hardware is placed by its axis: `direction` is the hardware's local +Z (from the screw tip towards the head),
  and `origin` is where its local z = 0 lands: the head's seating plane for a screw, the face away from the head
  for a nut or washer (the nut or washer runs from `origin` along +direction).
"""
from __future__ import annotations

import math
import numbers
from dataclasses import KW_ONLY, dataclass, field

from build123d import Location, Shape

import hardware
from assembly_geometry import TARGET_TOL

ROLES = ("clearance", "thread", "insert")
AXES = ("x", "y", "z")


# --- placement ----------------------------------------------------------------------------------------------------


def place(x=0, y=0, z=0, *, rx=0, ry=0, rz=0) -> Location:
    """A Location: rotate rx, then ry, then rz degrees about the X, Y and Z axes, then move by (x, y, z)."""
    for key, value in (("x", x), ("y", y), ("z", z), ("rx", rx), ("ry", ry), ("rz", rz)):
        _number(f"place({key}=...)", value)
    loc = Location((float(x), float(y), float(z)))
    for axis, angle in (((0, 0, 1), rz), ((0, 1, 0), ry), ((1, 0, 0), rx)):
        if angle:
            loc = loc * Location((0, 0, 0), axis, float(angle))
    return loc


def flip() -> Location:
    """A Location that rotates 180 degrees about X (a part modelled upside down, e.g. a lid printed plate-down)."""
    return Location((0, 0, 0), (1, 0, 0), 180.0)


# --- field checks -------------------------------------------------------------------------------------------------


def _where(obj, fld):
    name = getattr(obj, "name", None)
    label = type(obj).__name__ if not isinstance(name, str) else f"{type(obj).__name__} {name!r}"
    return f"{label}: {fld}"


def _number(what, value, *, positive=False, nonneg=False):
    if not isinstance(value, numbers.Real) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    if positive and value <= 0:
        raise ValueError(f"{what} must be positive, got {value!r}")
    if nonneg and value < 0:
        raise ValueError(f"{what} must be zero or more, got {value!r}")
    return float(value)


def _name(obj, fld="name"):
    value = getattr(obj, fld)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{_where(obj, fld)} must be a non-empty string, got {value!r}")
    return value


def _opt_name(obj, fld):
    return None if getattr(obj, fld) is None else _name(obj, fld)


def _source(obj):
    value = obj.source
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{_where(obj, 'source')} must say where the expected value comes from, "
            f"e.g. 'design.md § Joining' or 'confirmed by the user 2026-10-21'; got {value!r}"
        )


def _vec(obj, fld, *, unit=False, optional=False):
    value = getattr(obj, fld)
    if value is None and optional:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{_where(obj, fld)} must be (x, y, z), got {value!r}")
    vec = tuple(_number(f"{_where(obj, fld)}[{i}]", v) for i, v in enumerate(value))
    if unit:
        length = math.sqrt(sum(v * v for v in vec))
        if length < 1e-9:
            raise ValueError(f"{_where(obj, fld)} must be a non-zero direction, got {value!r}")
        vec = tuple(v / length for v in vec)
    return vec


def _num_field(obj, fld, *, optional=False, **kw):
    value = getattr(obj, fld)
    if value is None and optional:
        return None
    return _number(_where(obj, fld), value, **kw)


def _range(obj, fld):
    value = getattr(obj, fld)
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"{_where(obj, fld)} must be (min, max) in mm or None, got {value!r}")
    lo, hi = (_number(f"{_where(obj, fld)}", v) for v in value)
    if lo > hi:
        raise ValueError(f"{_where(obj, fld)} must be (min, max) with min <= max, got {value!r}")
    return (lo, hi)


def _location(obj, fld):
    value = getattr(obj, fld)
    if not isinstance(value, Location):
        raise ValueError(f"{_where(obj, fld)} must be a build123d Location, e.g. place(z=10, rx=180); got {value!r}")


def _color(obj):
    value = obj.color
    if value is None:
        return None
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    elif (isinstance(value, (tuple, list)) and len(value) == 3
          and all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in value)):
        return tuple(value)
    raise ValueError(f"{_where(obj, 'color')} must be (r, g, b) with 0..255 integers or '#rrggbb', got {value!r}")


def _choice(obj, fld, valid):
    value = getattr(obj, fld)
    if value not in valid:
        raise ValueError(f"{_where(obj, fld)} is {value!r}; valid: {', '.join(map(str, valid))}")


def _names(obj, fld):
    value = getattr(obj, fld)
    if isinstance(value, str) or not isinstance(value, (tuple, list)) or not value:
        raise ValueError(f"{_where(obj, fld)} must be a non-empty list of item names, got {value!r}")
    for v in value:
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{_where(obj, fld)} must hold item names (strings), got {v!r}")
    return tuple(value)


def _limit(obj):
    obj.limit = _num_field(obj, "limit", optional=True, positive=True)


# --- items --------------------------------------------------------------------------------------------------------


@dataclass
class Part:
    """A part model placed in the unit. `model` is relative to assembly.py; `params` override its PARAMETERS.

    params may hold "_part": "<name>" to pick one shape of a model whose build() returns several.
    """

    name: str
    model: str
    at: Location = field(default_factory=Location)
    params: dict | None = None
    color: tuple | str | None = None

    def __post_init__(self):
        _name(self)
        if not isinstance(self.model, str) or not self.model.strip().lower().endswith(".py"):
            raise ValueError(f"{_where(self, 'model')} must be a .py path relative to assembly.py, got {self.model!r}")
        _location(self, "at")
        params = {} if self.params is None else self.params
        if not isinstance(params, dict) or not all(isinstance(k, str) for k in params):
            raise ValueError(f"{_where(self, 'params')} must be a dict of parameter name -> value, got {self.params!r}")
        if "_part" in params and not isinstance(params["_part"], str):
            raise ValueError(f"{_where(self, 'params')}['_part'] must be the name of one of the model's parts")
        self.params = dict(params)
        self.color = _color(self)


@dataclass
class Screw:
    """A screw stand-in from the hardware catalogue: head seating plane at `origin`, local +Z along `direction`."""

    name: str
    size: str
    kind: str
    length: float
    origin: tuple
    direction: tuple
    color: tuple | str | None = None

    def __post_init__(self):
        _name(self)
        _choice(self, "kind", hardware.SCREW_KINDS)
        _choice(self, "size", tuple(hardware.HEADS[self.kind]))
        self.length = _num_field(self, "length", positive=True)
        self.origin = _vec(self, "origin")
        self.direction = _vec(self, "direction", unit=True)
        self.color = _color(self)


@dataclass
class Nut:
    """A nut stand-in: its z = 0 face (away from the head) at `origin`, running along +`direction`."""

    name: str
    size: str
    kind: str
    origin: tuple
    direction: tuple
    color: tuple | str | None = None

    def __post_init__(self):
        _name(self)
        _choice(self, "kind", hardware.NUT_KINDS)
        _choice(self, "size", tuple(hardware.PITCH))
        self.origin = _vec(self, "origin")
        self.direction = _vec(self, "direction", unit=True)
        self.color = _color(self)


@dataclass
class Washer:
    """A washer stand-in: its z = 0 face at `origin`, running along +`direction`."""

    name: str
    size: str
    origin: tuple
    direction: tuple
    color: tuple | str | None = None

    def __post_init__(self):
        _name(self)
        _choice(self, "size", tuple(hardware.WASHERS))
        self.origin = _vec(self, "origin")
        self.direction = _vec(self, "direction", unit=True)
        self.color = _color(self)


@dataclass
class StandIn:
    """A project stand-in (bought component, cable, strip): a build123d shape made in assembly.py."""

    name: str
    shape: Shape
    at: Location = field(default_factory=Location)
    color: tuple | str | None = None

    def __post_init__(self):
        _name(self)
        if not isinstance(self.shape, Shape):
            raise ValueError(f"{_where(self, 'shape')} must be a build123d shape, e.g. Box(20, 10, 4); got {self.shape!r}")
        _location(self, "at")
        self.color = _color(self)


ITEM_TYPES = (Part, Screw, Nut, Washer, StandIn)
HARDWARE_TYPES = (Screw, Nut, Washer)


# --- interfaces ---------------------------------------------------------------------------------------------------
# references() lists (field, item name, required item type or None) for the checks at Unit/Assembly level.


@dataclass
class Bolted:
    """A screw on its axis through parts; `through` is [(item, role)], role "clearance", "thread" or "insert"."""

    name: str
    screw: str
    through: list
    _: KW_ONLY
    nut: str | None = None
    source: str
    limit: float | None = None
    protrusion: float | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "screw")
        _opt_name(self, "nut")
        if isinstance(self.through, str) or not isinstance(self.through, (tuple, list)) or not self.through:
            raise ValueError(f"{_where(self, 'through')} must be a non-empty list of (item, role), got {self.through!r}")
        pairs = []
        for entry in self.through:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2 or not isinstance(entry[0], str):
                raise ValueError(f"{_where(self, 'through')} entries must be (item, role), got {entry!r}")
            if entry[1] not in ROLES:
                raise ValueError(
                    f"{_where(self, 'through')}: role {entry[1]!r} for {entry[0]!r} is not valid; "
                    f"valid roles: {', '.join(ROLES)}"
                )
            pairs.append((entry[0], entry[1]))
        self.through = tuple(pairs)
        _source(self)
        _limit(self)
        self.protrusion = _num_field(self, "protrusion", optional=True, nonneg=True)

    def references(self):
        refs = [("screw", self.screw, Screw)] + [("through", item, None) for item, _ in self.through]
        return refs + ([("nut", self.nut, Nut)] if self.nut is not None else [])


@dataclass
class NutPocket:
    """A nut in its pocket in `part`: no overlap, can't turn, on its fastener's axis."""

    name: str
    nut: str
    part: str
    _: KW_ONLY
    source: str
    limit: float | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "nut")
        _name(self, "part")
        _source(self)
        _limit(self)

    def references(self):
        return [("nut", self.nut, Nut), ("part", self.part, None)]


@dataclass
class Snap:
    """A bump on `bump` into a catch on `catch`, checked in the section through `point` with this `normal`.

    `insert_dir` is the direction the bump part moves to snap in. `engagement` and `clearance` are (min, max)
    in mm or None; `fit` optionally names a profile fit to report alongside; `travel` is how far to slide back.
    """

    name: str
    bump: str
    catch: str
    point: tuple
    normal: tuple
    insert_dir: tuple
    engagement: tuple | None
    clearance: tuple | None
    _: KW_ONLY
    fit: str | None = None
    travel: float = 10.0
    source: str
    limit: float | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "bump")
        _name(self, "catch")
        self.point = _vec(self, "point")
        self.normal = _vec(self, "normal", unit=True)
        self.insert_dir = _vec(self, "insert_dir", unit=True)
        if abs(sum(a * b for a, b in zip(self.normal, self.insert_dir))) > 1e-6:
            raise ValueError(f"{_where(self, 'insert_dir')} must lie in the section plane (perpendicular to normal)")
        self.engagement = _range(self, "engagement")
        self.clearance = _range(self, "clearance")
        _opt_name(self, "fit")
        self.travel = _num_field(self, "travel", positive=True)
        _source(self)
        _limit(self)

    def references(self):
        return [("bump", self.bump, None), ("catch", self.catch, None)]


@dataclass
class Seat:
    """Faces of `a` and `b` near the plane (point, normal) touch: no gap above the limit, no overlap."""

    name: str
    a: str
    b: str
    point: tuple
    normal: tuple
    _: KW_ONLY
    source: str
    limit: float | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "a")
        _name(self, "b")
        self.point = _vec(self, "point")
        self.normal = _vec(self, "normal", unit=True)
        _source(self)
        _limit(self)

    def references(self):
        return [("a", self.a, None), ("b", self.b, None)]


@dataclass
class Passage:
    """`item` (a straight stand-in) passes through an opening of every part in `through`, without overlap and with
    at least `min_clearance`.

    `direction` is the stand-in's run direction; None: its longest bounding-box axis.
    """

    name: str
    item: str
    through: list
    min_clearance: float
    _: KW_ONLY
    direction: tuple | None = None
    source: str
    limit: float | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "item")
        self.through = _names(self, "through")
        self.min_clearance = _num_field(self, "min_clearance", nonneg=True)
        self.direction = _vec(self, "direction", unit=True, optional=True)
        _source(self)
        _limit(self)

    def references(self):
        return [("item", self.item, None)] + [("through", n, None) for n in self.through]


@dataclass
class Clear:
    """`a` and `b` keep at least `min_distance` apart."""

    name: str
    a: str
    b: str
    min_distance: float
    _: KW_ONLY
    source: str
    limit: float | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "a")
        _name(self, "b")
        self.min_distance = _num_field(self, "min_distance", nonneg=True)
        _source(self)
        _limit(self)

    def references(self):
        return [("a", self.a, None), ("b", self.b, None)]


INTERFACE_TYPES = (Bolted, NutPocket, Snap, Seat, Passage, Clear)


# --- targets ------------------------------------------------------------------------------------------------------
# value None is allowed on purpose: the check then fails with "no target:", so a missing number shows up.


def _target(obj):
    _name(obj)
    obj.value = _num_field(obj, "value", optional=True)
    _source(obj)
    obj.tol = _num_field(obj, "tol", nonneg=True)


@dataclass
class Size:
    """The combined bounding size of `items` along `axis` ("x", "y" or "z")."""

    name: str
    items: list
    axis: str
    value: float | None
    _: KW_ONLY
    source: str
    tol: float = TARGET_TOL

    def __post_init__(self):
        self.items = _names(self, "items")
        _choice(self, "axis", AXES)
        _target(self)

    def references(self):
        return [("items", n, None) for n in self.items]


@dataclass
class Gap:
    """The distance between `a` and `b`; signed along `direction` when one is given (None: smallest distance)."""

    name: str
    a: str
    b: str
    direction: tuple | None
    value: float | None
    _: KW_ONLY
    source: str
    tol: float = TARGET_TOL

    def __post_init__(self):
        _name(self, "a")
        _name(self, "b")
        self.direction = _vec(self, "direction", unit=True, optional=True)
        _target(self)

    def references(self):
        return [("a", self.a, None), ("b", self.b, None)]


@dataclass
class Step:
    """probe(a from origin_a) minus probe(b from origin_b), both along `direction`: 0 when the faces are flush."""

    name: str
    a: str
    b: str
    origin_a: tuple
    origin_b: tuple
    direction: tuple
    value: float | None
    _: KW_ONLY
    source: str
    tol: float = TARGET_TOL

    def __post_init__(self):
        _name(self, "a")
        _name(self, "b")
        self.origin_a = _vec(self, "origin_a")
        self.origin_b = _vec(self, "origin_b")
        self.direction = _vec(self, "direction", unit=True)
        _target(self)

    def references(self):
        return [("a", self.a, None), ("b", self.b, None)]


@dataclass
class Position:
    """Where a probe from `origin` along `direction` first hits `item`."""

    name: str
    item: str
    origin: tuple
    direction: tuple
    value: float | None
    _: KW_ONLY
    source: str
    tol: float = TARGET_TOL

    def __post_init__(self):
        _name(self, "item")
        self.origin = _vec(self, "origin")
        self.direction = _vec(self, "direction", unit=True)
        _target(self)

    def references(self):
        return [("item", self.item, None)]


TARGET_TYPES = (Size, Gap, Step, Position)


# --- units, layouts, the assembly ---------------------------------------------------------------------------------


def _typed_list(obj, fld, types, what):
    value = getattr(obj, fld)
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ValueError(f"{_where(obj, fld)} must be a list of {what}, got {value!r}")
    for entry in value:
        if not isinstance(entry, types):
            raise ValueError(
                f"{_where(obj, fld)} holds a {type(entry).__name__}; expected {what} "
                f"({', '.join(t.__name__ for t in types)})"
            )
    return tuple(value)


def _unique(obj, fld, names):
    seen = set()
    for n in names:
        if n in seen:
            raise ValueError(f"{_where(obj, fld)}: the name {n!r} is used twice")
        seen.add(n)


def _check_refs(owner, decls, lookup):
    """Every name an interface or target refers to must resolve (lookup(name) -> item or None) to the right type."""
    for decl in decls:
        for fld, ref, need in decl.references():
            item = lookup(ref)
            if item is None:
                raise ValueError(f"{owner}: {type(decl).__name__} {decl.name!r} {fld} refers to unknown item {ref!r}")
            if need is not None and not isinstance(item, need):
                raise ValueError(
                    f"{owner}: {type(decl).__name__} {decl.name!r} {fld} must be a {need.__name__}, "
                    f"but {ref!r} is a {type(item).__name__}"
                )


@dataclass
class Unit:
    """A named group of placed items in its own frame, with its interfaces and targets."""

    name: str
    items: list
    interfaces: list = ()
    targets: list = ()

    def __post_init__(self):
        _name(self)
        self.items = _typed_list(self, "items", ITEM_TYPES, "items")
        if not self.items:
            raise ValueError(f"{_where(self, 'items')} must hold at least one item")
        self.interfaces = _typed_list(self, "interfaces", INTERFACE_TYPES, "interfaces")
        self.targets = _typed_list(self, "targets", TARGET_TYPES, "targets")
        _unique(self, "items", [i.name for i in self.items])
        _unique(self, "interfaces", [i.name for i in self.interfaces])
        _unique(self, "targets", [t.name for t in self.targets])
        for item in self.items:
            if "." in item.name:
                raise ValueError(f"{_where(self, 'items')}: item name {item.name!r} must not contain '.'")
        by_name = {i.name: i for i in self.items}
        _check_refs(f"Unit {self.name!r}", self.interfaces + self.targets, by_name.get)

    def item(self, name):
        return next((i for i in self.items if i.name == name), None)


@dataclass
class Layout:
    """Unit instances side by side, plus layout items, interfaces and targets.

    instances: [(instance_name, unit_name, Location)]. exact: the instance names checked with full solids.
    overrides: {instance: {item: params}} for per-instance part variants. References use "<instance>.<item>".
    """

    name: str
    instances: list
    items: list = ()
    interfaces: list = ()
    targets: list = ()
    exact: tuple = ()
    overrides: dict | None = None

    def __post_init__(self):
        _name(self)
        if isinstance(self.instances, str) or not isinstance(self.instances, (tuple, list)) or not self.instances:
            raise ValueError(f"{_where(self, 'instances')} must be a non-empty list of (instance, unit, Location)")
        rows = []
        for entry in self.instances:
            if (not isinstance(entry, (tuple, list)) or len(entry) != 3 or not isinstance(entry[0], str)
                    or not isinstance(entry[1], str) or not isinstance(entry[2], Location)):
                raise ValueError(f"{_where(self, 'instances')} entries must be (instance, unit, Location), got {entry!r}")
            if not entry[0].strip() or "." in entry[0]:
                raise ValueError(f"{_where(self, 'instances')}: instance name {entry[0]!r} must be non-empty, without '.'")
            rows.append(tuple(entry))
        self.instances = tuple(rows)
        _unique(self, "instances", [r[0] for r in rows])
        self.items = _typed_list(self, "items", ITEM_TYPES, "items")
        self.interfaces = _typed_list(self, "interfaces", INTERFACE_TYPES, "interfaces")
        self.targets = _typed_list(self, "targets", TARGET_TYPES, "targets")
        _unique(self, "items", [i.name for i in self.items])
        _unique(self, "interfaces", [i.name for i in self.interfaces])
        _unique(self, "targets", [t.name for t in self.targets])
        names = {r[0] for r in rows}
        if isinstance(self.exact, str):
            raise ValueError(f"{_where(self, 'exact')} must be a list of instance names, got {self.exact!r}")
        self.exact = tuple(self.exact)
        for inst in self.exact:
            if inst not in names:
                raise ValueError(f"{_where(self, 'exact')}: unknown instance {inst!r}; instances: {', '.join(sorted(names))}")
        overrides = {} if self.overrides is None else self.overrides
        if not isinstance(overrides, dict):
            raise ValueError(f"{_where(self, 'overrides')} must be {{instance: {{item: params}}}}, got {self.overrides!r}")
        for inst, per_item in overrides.items():
            if inst not in names:
                raise ValueError(f"{_where(self, 'overrides')}: unknown instance {inst!r}; instances: {', '.join(sorted(names))}")
            if not isinstance(per_item, dict) or not all(isinstance(p, dict) for p in per_item.values()):
                raise ValueError(f"{_where(self, 'overrides')}[{inst!r}] must be {{item: params dict}}")
        self.overrides = {inst: {item: dict(p) for item, p in per.items()} for inst, per in overrides.items()}

    def unit_of(self, instance):
        return next((r[1] for r in self.instances if r[0] == instance), None)


@dataclass
class Assembly:
    """What build_assembly(a) returns: the unit types, the layouts, and notes copied into the result's warnings."""

    units: list
    layouts: list = ()
    notes: list = ()

    def __post_init__(self):
        self.units = _typed_list(self, "units", (Unit,), "units")
        if not self.units:
            raise ValueError("Assembly: units must hold at least one Unit")
        self.layouts = _typed_list(self, "layouts", (Layout,), "layouts")
        if isinstance(self.notes, str) or not all(isinstance(n, str) for n in self.notes):
            raise ValueError(f"Assembly: notes must be a list of strings, got {self.notes!r}")
        self.notes = tuple(self.notes)
        _unique(self, "units", [u.name for u in self.units])
        _unique(self, "layouts", [lay.name for lay in self.layouts])
        units = {u.name: u for u in self.units}
        for lay in self.layouts:
            for inst, unit_name, _ in lay.instances:
                if unit_name not in units:
                    raise ValueError(
                        f"Layout {lay.name!r}: instance {inst!r} uses unknown unit {unit_name!r}; "
                        f"units: {', '.join(units)}"
                    )
            for inst, per_item in lay.overrides.items():
                unit = units[lay.unit_of(inst)]
                for item_name in per_item:
                    if not isinstance(unit.item(item_name), Part):
                        raise ValueError(
                            f"Layout {lay.name!r}: overrides[{inst!r}] names {item_name!r}, which is not a Part "
                            f"of unit {unit.name!r}"
                        )
            layout_items = {i.name: i for i in lay.items}

            def lookup(ref, lay=lay, layout_items=layout_items):
                inst, dot, item_name = ref.partition(".")
                if not dot:
                    return layout_items.get(ref)
                unit_name = lay.unit_of(inst)
                return None if unit_name is None else units[unit_name].item(item_name)

            _check_refs(f"Layout {lay.name!r}", lay.interfaces + lay.targets, lookup)

    def unit(self, name):
        return next((u for u in self.units if u.name == name), None)
