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
from dataclasses import replace as dataclass_replace

from OCP.gp import gp_Pnt, gp_Vec
from build123d import Location, Plane, Shape

import hardware
from assembly_geometry import TARGET_TOL

ROLES = ("clearance", "thread", "insert")
MATERIALS = ("matte", "metal", "translucent", "emissive")
DEFAULT_GLOW = 10.0  # Blender emission strength of an emissive look without `glow`
BACKDROPS = ("floor", "wall")
LOOK_KEYS = ("color", "material", "glow")
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


def _look(obj, *, partial=False):
    """Validate `material` and `glow` (positive, emissive only), which every item shares.

    partial: a Layout.looks entry, merged over the item's own look later, may give `glow` without `material`
    (the merged look is checked when the scene is made).
    """
    if obj.material is not None:
        _choice(obj, "material", MATERIALS)
    obj.glow = _num_field(obj, "glow", optional=True, positive=True)
    if obj.glow is not None and obj.material != "emissive" and not (partial and obj.material is None):
        raise ValueError(f"{_where(obj, 'glow')} is only for material='emissive', got material {obj.material!r}")


def _explode(obj):
    obj.explode = _vec(obj, "explode", optional=True)


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
    material: str | None = None
    glow: float | None = None
    explode: tuple | None = None

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
        _look(self)
        _explode(self)


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
    material: str | None = None
    glow: float | None = None
    explode: tuple | None = None
    tool: bool = True

    def __post_init__(self):
        _name(self)
        _choice(self, "kind", hardware.SCREW_KINDS)
        _choice(self, "size", tuple(hardware.HEADS[self.kind]))
        self.length = _num_field(self, "length", positive=True)
        self.origin = _vec(self, "origin")
        self.direction = _vec(self, "direction", unit=True)
        self.color = _color(self)
        _look(self)
        _explode(self)
        if not isinstance(self.tool, bool):
            raise ValueError(f"{_where(self, 'tool')} must be True or False, got {self.tool!r}")


@dataclass
class Nut:
    """A nut stand-in: its z = 0 face (away from the head) at `origin`, running along +`direction`.

    `angle` turns the hex about its own axis (degrees, right-handed about `direction`), after the frame is set. At
    angle 0 one corner points along the frame's local +X, which is the x_dir that build123d's
    Plane(origin, z_dir=direction) picks: +X for direction +Z, -X for -Z, +Z for +X and +Y, -Z for -X and -Y.
    """

    name: str
    size: str
    kind: str
    origin: tuple
    direction: tuple
    color: tuple | str | None = None
    angle: float = 0.0
    material: str | None = None
    glow: float | None = None
    explode: tuple | None = None

    def __post_init__(self):
        _name(self)
        _choice(self, "kind", hardware.NUT_KINDS)
        _choice(self, "size", tuple(hardware.PITCH))
        self.origin = _vec(self, "origin")
        self.direction = _vec(self, "direction", unit=True)
        self.color = _color(self)
        self.angle = _num_field(self, "angle")
        _look(self)
        _explode(self)


@dataclass
class Washer:
    """A washer stand-in: its z = 0 face at `origin`, running along +`direction`."""

    name: str
    size: str
    origin: tuple
    direction: tuple
    color: tuple | str | None = None
    material: str | None = None
    glow: float | None = None
    explode: tuple | None = None

    def __post_init__(self):
        _name(self)
        _choice(self, "size", tuple(hardware.WASHERS))
        self.origin = _vec(self, "origin")
        self.direction = _vec(self, "direction", unit=True)
        self.color = _color(self)
        _look(self)
        _explode(self)


@dataclass
class StandIn:
    """A project stand-in (bought component, cable, strip): a build123d shape made in assembly.py."""

    name: str
    shape: Shape
    at: Location = field(default_factory=Location)
    color: tuple | str | None = None
    material: str | None = None
    glow: float | None = None
    explode: tuple | None = None

    def __post_init__(self):
        _name(self)
        if not isinstance(self.shape, Shape):
            raise ValueError(f"{_where(self, 'shape')} must be a build123d shape, e.g. Box(20, 10, 4); got {self.shape!r}")
        _location(self, "at")
        self.color = _color(self)
        _look(self)
        _explode(self)


ITEM_TYPES = (Part, Screw, Nut, Washer, StandIn)
HARDWARE_TYPES = (Screw, Nut, Washer)


# --- interfaces ---------------------------------------------------------------------------------------------------
# references() lists (field, item name, required item type or None) for the checks at Unit/Assembly level.


@dataclass
class Bolted:
    """A screw on its axis through parts; `through` is [(item, role)], role "clearance", "thread" or "insert".

    `washer` names a Washer under the head, `nut_washer` one between the parts and the nut (it needs `nut`).
    """

    name: str
    screw: str
    through: list
    _: KW_ONLY
    nut: str | None = None
    source: str
    limit: float | None = None
    protrusion: float | None = None
    washer: str | None = None
    nut_washer: str | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "screw")
        _opt_name(self, "nut")
        _opt_name(self, "washer")
        _opt_name(self, "nut_washer")
        if self.nut_washer is not None and self.nut is None:
            raise ValueError(f"{_where(self, 'nut_washer')} is the washer under the nut, so it needs nut= too")
        if self.washer is not None and self.washer == self.nut_washer:
            raise ValueError(f"{_where(self, 'washer')} and nut_washer must be two different washers")
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
        refs += [("nut", self.nut, Nut)] if self.nut is not None else []
        return refs + [(fld, getattr(self, fld), Washer) for fld in ("washer", "nut_washer")
                       if getattr(self, fld) is not None]


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
    in mm or None; a None engagement fails the check as "no target" (a None clearance is just not checked).
    `fit` optionally names a profile fit to report alongside; `travel` is how far to slide back.
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
    `ends_inside=True` lets the stand-in end inside an opening of a part it entered from one of that part's faces
    (a strip cut to end in a slanted wall's slot); by default an end inside an opening fails.
    """

    name: str
    item: str
    through: list
    min_clearance: float
    _: KW_ONLY
    direction: tuple | None = None
    ends_inside: bool = False
    source: str
    limit: float | None = None

    def __post_init__(self):
        _name(self)
        _name(self, "item")
        self.through = _names(self, "through")
        self.min_clearance = _num_field(self, "min_clearance", nonneg=True)
        self.direction = _vec(self, "direction", unit=True, optional=True)
        if not isinstance(self.ends_inside, bool):
            raise ValueError(f"{_where(self, 'ends_inside')} must be True or False, got {self.ends_inside!r}")
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
# A target is either `value` +/- `tol`, or bounds: `min` and/or `max` (mm, open ends allowed).
# No value and no bounds is allowed on purpose: the check then fails with "no target:", so a missing number shows up.


def _target(obj):
    _name(obj)
    obj.value = _num_field(obj, "value", optional=True)
    obj.min = _num_field(obj, "min", optional=True)
    obj.max = _num_field(obj, "max", optional=True)
    if obj.value is not None and (obj.min is not None or obj.max is not None):
        raise ValueError(
            f"{_where(obj, 'value')} and min/max are both given; a target is either value (with tol) "
            "or min and/or max, not both"
        )
    if obj.min is not None and obj.max is not None and obj.min > obj.max:
        raise ValueError(f"{_where(obj, 'min')} must be at most max, got min {obj.min!r} and max {obj.max!r}")
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
    min: float | None = None
    max: float | None = None

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
    min: float | None = None
    max: float | None = None

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
    min: float | None = None
    max: float | None = None

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
    min: float | None = None
    max: float | None = None

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
class Render:
    """How a unit or layout is staged for `print3d render`: on the floor, or against a wall.

    facing: for "wall", the vector (normalised) of the side of the object that faces away from the wall.
    """

    backdrop: str = "floor"
    facing: tuple | None = None

    def __post_init__(self):
        _choice(self, "backdrop", BACKDROPS)
        self.facing = _vec(self, "facing", unit=True, optional=True)
        if self.backdrop == "wall" and self.facing is None:
            raise ValueError(f"{_where(self, 'facing')} is required for backdrop 'wall': the side facing away from the wall")


def _render(obj):
    if obj.render is not None and not isinstance(obj.render, Render):
        raise ValueError(f"{_where(obj, 'render')} must be a Render(...) or None, got {obj.render!r}")


class _LookProbe:
    """One look entry run through the item checks; `name` labels the errors. partial: see _look."""

    def __init__(self, name, color=None, material=None, glow=None, *, partial=False):
        self.name, self.color, self.material, self.glow = name, color, material, glow
        self.color = _color(self)
        _look(self, partial=partial)


@dataclass
class Unit:
    """A named group of placed items in its own frame, with its interfaces and targets."""

    name: str
    items: list
    interfaces: list = ()
    targets: list = ()
    render: Render | None = None

    def __post_init__(self):
        _name(self)
        _render(self)
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
    looks: {instance: {item: {"color" | "material" | "glow": value}}}, per-instance appearance of any item type.
    """

    name: str
    instances: list
    items: list = ()
    interfaces: list = ()
    targets: list = ()
    exact: tuple = ()
    overrides: dict | None = None
    render: Render | None = None
    looks: dict | None = None

    def __post_init__(self):
        _name(self)
        _render(self)
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
        for item in self.items:  # "<instance>.<item>" names the instances' items
            if "." in item.name:
                raise ValueError(f"{_where(self, 'items')}: item name {item.name!r} must not contain '.'")
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
        looks = {} if self.looks is None else self.looks
        shape = f"{_where(self, 'looks')} must be {{instance: {{item: {{{'|'.join(LOOK_KEYS)}: value}}}}}}"
        if not isinstance(looks, dict):
            raise ValueError(f"{shape}, got {self.looks!r}")
        checked = {}
        for inst, per_item in looks.items():
            if inst not in names:
                raise ValueError(f"{_where(self, 'looks')}: unknown instance {inst!r}; instances: {', '.join(sorted(names))}")
            if not isinstance(per_item, dict) or not all(isinstance(v, dict) for v in per_item.values()):
                raise ValueError(f"{shape}; [{inst!r}] is {per_item!r}")
            checked[inst] = {}
            for item, values in per_item.items():
                where = f"{_where(self, 'looks')}[{inst!r}][{item!r}]"
                bad = [k for k in values if k not in LOOK_KEYS]
                if bad:
                    raise ValueError(f"{where}: unknown key {bad[0]!r}; valid: {', '.join(LOOK_KEYS)}")
                probe = _LookProbe(where, **values, partial=True)
                checked[inst][item] = {k: getattr(probe, k) for k in LOOK_KEYS if k in values}
        self.looks = checked

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
            for inst, per_item in lay.looks.items():
                unit = units[lay.unit_of(inst)]
                for item_name in per_item:
                    if unit.item(item_name) is None:
                        raise ValueError(
                            f"Layout {lay.name!r}: looks[{inst!r}] names {item_name!r}, which is not an item "
                            f"of unit {unit.name!r}; items: {', '.join(i.name for i in unit.items)}"
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


# --- patterns -----------------------------------------------------------------------------------------------------
# polar/linear/grid make Locations; repeat(template, locations) copies one declaration onto each of them.


def _count(where, value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{where} must be a positive whole number, got {value!r}")
    return value


def _triple(where, value, *, nonzero=False):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError(f"{where} must be (x, y, z), got {value!r}")
    vec = tuple(_number(f"{where}[{i}]", v) for i, v in enumerate(value))
    if nonzero and math.sqrt(sum(v * v for v in vec)) < 1e-9:
        raise ValueError(f"{where} must be a non-zero direction, got {value!r}")
    return vec


def polar(n, *, start_deg=0.0, step_deg=None, axis=(0, 0, 1), center=(0, 0, 0)) -> list[Location]:
    """n Locations rotating about `axis` through `center`: start_deg, start_deg + step_deg, ... (step: 360 / n)."""
    n = _count("polar: n", n)
    start = _number("polar: start_deg", start_deg)
    step = 360.0 / n if step_deg is None else _number("polar: step_deg", step_deg)
    axis = _triple("polar: axis", axis, nonzero=True)
    center = _triple("polar: center", center)
    to_center, from_center = Location(center), Location(tuple(-v for v in center))
    return [to_center * Location((0, 0, 0), axis, start + k * step) * from_center for k in range(n)]


def linear(n, step, *, start=(0, 0, 0)) -> list[Location]:
    """n Locations moving by start, start + step, start + 2 * step, ... (step and start are (dx, dy, dz) in mm)."""
    n = _count("linear: n", n)
    step = _triple("linear: step", step)
    start = _triple("linear: start", start)
    return [Location(tuple(s + k * d for s, d in zip(start, step))) for k in range(n)]


def grid(nx, ny, dx, dy, *, start=(0, 0, 0)) -> list[Location]:
    """nx * ny Locations dx apart along X and dy apart along Y, row by row (x varies fastest)."""
    nx, ny = _count("grid: nx", nx), _count("grid: ny", ny)
    dx, dy = _number("grid: dx", dx), _number("grid: dy", dy)
    x0, y0, z0 = _triple("grid: start", start)
    return [Location((x0 + i * dx, y0 + j * dy, z0)) for j in range(ny) for i in range(nx)]


# Per declaration type: fields moved as points, fields turned as directions, fields that name other items.
_PATTERN_FIELDS = {
    Part: ((), ("explode",), ()),
    StandIn: ((), ("explode",), ()),
    Screw: (("origin",), ("direction", "explode"), ()),
    Nut: (("origin",), ("direction", "explode"), ()),
    Washer: (("origin",), ("direction", "explode"), ()),
    Bolted: ((), (), ("screw", "nut", "through", "washer", "nut_washer")),
    NutPocket: ((), (), ("nut", "part")),
    Snap: (("point",), ("normal", "insert_dir"), ("bump", "catch")),
    Seat: (("point",), ("normal",), ("a", "b")),
    Passage: ((), ("direction",), ("item", "through")),
    Clear: ((), (), ("a", "b")),
    Size: ((), (), ("items",)),
    Gap: ((), ("direction",), ("a", "b")),
    Step: (("origin_a", "origin_b"), ("direction",), ("a", "b")),
    Position: (("origin",), ("direction",), ("item",)),
}


def _pattern_text(where, text, **values):
    if "{" not in text and "}" not in text:
        return text
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError, AttributeError) as exc:
        keys = " and ".join("{" + k + "}" for k in values)
        raise ValueError(f"repeat: {where} {text!r} can't be filled in ({exc!r}); it may use only {keys}") from None


def _moved(loc, value, *, direction):
    """A point moved by `loc`, or (direction=True) a direction turned by its rotation only."""
    trsf = loc.wrapped.Transformation()
    v = (gp_Vec if direction else gp_Pnt)(*value).Transformed(trsf)
    return tuple(0.0 if abs(c) < 1e-12 else c for c in (v.X(), v.Y(), v.Z()))


def _nut_zero(direction):
    """The local +X of a nut's frame: where its corner points at angle 0."""
    return tuple(Plane(origin=(0, 0, 0), z_dir=direction).x_dir)


def _turned_angle(template, loc, direction):
    """A Nut's angle after `loc` turned it (and its direction to `direction`): its corners turn with the copy."""
    d0, x0 = template.direction, _nut_zero(template.direction)
    a = math.radians(template.angle)
    y0 = _cross(d0, x0)
    corner = tuple(math.cos(a) * x + math.sin(a) * y for x, y in zip(x0, y0))
    turned = _moved(loc, corner, direction=True)
    x1 = _nut_zero(direction)
    y1 = _cross(direction, x1)
    angle = math.degrees(math.atan2(_dot(turned, y1), _dot(turned, x1)))
    return 0.0 if abs(angle) < 1e-9 else angle


def _dot(u, v):
    return sum(p * q for p, q in zip(u, v))


def _cross(u, v):
    return (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])


def _turned_axis(template, loc):
    """The Size axis after the rotation of `loc`: it must stay a principal axis."""
    unit = [1.0 if a == template.axis else 0.0 for a in AXES]
    for axis, c in zip(AXES, _moved(loc, unit, direction=True)):
        if abs(abs(c) - 1.0) < 1e-6:
            return axis
    raise ValueError(
        f"repeat: Size {template.name!r} axis {template.axis!r} is not along X, Y or Z after the rotation; "
        "rotate a Size only by multiples of 90 degrees about X, Y or Z"
    )


def repeat(template, locations, *, name=None) -> list:
    """Copies of one declaration (an item, interface or target), each moved by one of `locations`.

    Points (origins, section points) move with the Location; directions and normals only turn; a Part's or
    StandIn's `at` becomes loc * at; a Size axis turns with the rotation; a Nut's angle changes so its corners turn
    with the copy. Names: `name` is a pattern with {name}
    (the template's name) and {i} (0-based index); default "{name}-{i}", or the template's own name when that
    is a pattern itself ("bolt-{i}"). Names of other items (screw, nut, through, a, b, ...) that contain {i} get the same index; names
    without it are kept, so every copy can refer to one shared part. Each copy is validated like a hand-written
    declaration.
    """
    kind = type(template)
    if kind not in _PATTERN_FIELDS:
        raise ValueError(
            f"repeat: template must be an item, interface or target "
            f"({', '.join(t.__name__ for t in _PATTERN_FIELDS)}), got {kind.__name__}"
        )
    if isinstance(locations, Location) or not isinstance(locations, (tuple, list)):
        raise ValueError(f"repeat: locations must be a list of Locations, e.g. polar(6); got {locations!r}")
    for loc in locations:
        if not isinstance(loc, Location):
            raise ValueError(f"repeat: locations must hold build123d Locations, e.g. polar(6); got {loc!r}")
    own = "{" in template.name or "}" in template.name  # a templated name: "bolt-{i}"
    if name is None:
        name = template.name if own else "{name}-{i}"
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"repeat: name must be a pattern string such as '{{name}}-{{i}}', got {name!r}")
    points, directions, refs = _PATTERN_FIELDS[kind]
    label = f"{kind.__name__} {template.name!r}"
    copies = []
    for i, loc in enumerate(locations):
        base = _pattern_text("name", template.name, i=i) if own else template.name
        changes = {"name": _pattern_text("name", name, name=base, i=i)}
        for fld in refs:
            value = getattr(template, fld)
            if value is None:
                continue
            if isinstance(value, str):
                changes[fld] = _pattern_text(f"{label} {fld}", value, i=i)
            elif kind is Bolted:  # through: ((item, role), ...)
                changes[fld] = [(_pattern_text(f"{label} {fld}", item, i=i), role) for item, role in value]
            else:
                changes[fld] = [_pattern_text(f"{label} {fld}", item, i=i) for item in value]
        for fld in points:
            changes[fld] = _moved(loc, getattr(template, fld), direction=False)
        for fld in directions:
            if getattr(template, fld) is not None:
                changes[fld] = _moved(loc, getattr(template, fld), direction=True)
        if kind in (Part, StandIn):
            changes["at"] = loc * template.at
        if kind is Size:
            changes["axis"] = _turned_axis(template, loc)
        if kind is Nut:
            changes["angle"] = _turned_angle(template, loc, changes["direction"])
        copies.append(dataclass_replace(template, **changes))
    return copies
