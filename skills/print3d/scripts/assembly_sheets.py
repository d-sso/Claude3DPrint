"""print3d assembly evidence sheets: a section through each checked interface, and an overview per unit or layout.

Every sheet is drawn from the placed items that were measured (their shapes, tessellated at preview resolution),
cut along the planes the check reports, each item in its own colour. Same conventions as the part preview sheet
(render_model): orthographic panels with a title, an axis triad and a scale bar, plus an info panel; deterministic
PNGs with a long edge <= 1568 px. Never imports build123d (shapes only tessellate themselves).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

import render_model
from render_model import BACKGROUND, EDGE_RGB, GRID_RGB, MARKER_RGB, TITLE_H, Section, View

PALETTE = (  # colours for items that declare none, in item order
    (230, 120, 50),  # orange
    (70, 150, 220),  # blue
    (100, 185, 80),  # green
    (215, 85, 140),  # pink
    (150, 110, 210),  # purple
    (225, 195, 60),  # yellow
    (60, 185, 185),  # teal
    (175, 130, 90),  # brown
)
HARDWARE_RGB = (100, 100, 108)  # screws, nuts and washers without a declared colour: dark grey
HARDWARE_KINDS = ("screw", "nut", "washer")
IFACE_PANEL = 520  # px, square panel side on interface and overview sheets: 3 columns stay within 1568
ZOOM_MM = 40.0  # an interface panel shows a box this wide around the check's location...
ZOOM_PER_DIAMETER = 4.0  # ... or at least this many times the largest fastener diameter in the check
MAX_PLANES = 2
DETAIL = 5  # a check with one plane gets a second panel: the same cut magnified this much
FADE = 0.7  # items outside the highlight blend this far toward white
FAIL_RGB = (200, 30, 30)
PASS_RGB = (30, 130, 50)
FONT_SIZE, LINE_H, INFO_TOP = 12, 16, 10
SWATCH = 12  # px, legend colour square
CROSS = (4, 10)  # px, the location mark: a cross with a gap of 4 and arms to 10 around the centre


@dataclass(frozen=True)
class SceneItem:
    """One placed item ready to render: its mesh (in the unit or layout frame), colour and fastener diameter."""

    name: str
    kind: str
    mesh: object
    rgb: tuple
    diameter: float | None = None


def item_colors(items) -> dict:
    """{name: (r, g, b)}: the declared colour, else dark grey for hardware, else PALETTE in item order."""
    colours, k = {}, 0
    for name, item in items.items():
        if item.color is not None:
            colours[name] = tuple(item.color)
        elif item.kind in HARDWARE_KINDS:
            colours[name] = HARDWARE_RGB
        else:
            colours[name] = PALETTE[k % len(PALETTE)]
            k += 1
    return colours


def _diameter(item):
    """A hardware item's nominal diameter from its size ("M3" -> 3.0); None otherwise."""
    size = getattr(item.decl, "size", None)
    if item.kind not in HARDWARE_KINDS or not isinstance(size, str):
        return None
    try:
        return float(size.lstrip("Mm"))
    except ValueError:
        return None


def make_scene(items) -> list[SceneItem]:
    """Tessellate placed items {name: PlacedItem} once, for every sheet of a unit or layout."""
    colours = item_colors(items)
    return [SceneItem(name, item.kind, render_model.preview_mesh(item.shape), colours[name], _diameter(item))
            for name, item in items.items()]


def highlighted(name, highlight) -> bool:
    """An item is highlighted when nothing is, or its name or its instance ("c1" of "c1.block") is listed."""
    return not highlight or name in highlight or name.partition(".")[0] in highlight


def _faded(rgb):
    return tuple(round(v * (1 - FADE) + 255 * FADE) for v in rgb)


def zoom_box_mm(diameters) -> float:
    """The width of the box an interface panel shows: ZOOM_MM, or ZOOM_PER_DIAMETER x the largest diameter."""
    return max([ZOOM_MM] + [ZOOM_PER_DIAMETER * d for d in diameters if d])


# --- text -----------------------------------------------------------------------------------------------------------


def _num(v):
    return f"{round(float(v), 3):g}"


def _value(v):
    """A compact one-line rendering of a measured or expected value."""
    if isinstance(v, dict):
        return ", ".join(f"{k} {_value(x)}" for k, x in v.items()) or "-"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, bool) or v is None:
        return "-" if v is None else str(v).lower()
    if isinstance(v, (int, float)):
        return _num(v)
    return str(v)


def _point(p):
    return "(" + ", ".join("-" if v is None else _num(v) for v in p) + ")"


def _width(text):
    return render_model.font(FONT_SIZE).getlength(str(text).translate(render_model.ASCII_TEXT))


def _fit(text, width):
    """Text cut with an ellipsis so it is at most `width` px wide."""
    if _width(text) <= width:
        return text
    while text and _width(text + "…") > width:
        text = text[:-1]
    return text + "…"


def _wrap(text, width, max_lines, indent="    "):
    """Greedy word wrap into at most max_lines lines of at most `width` px (the last one cut with an ellipsis)."""
    lines, line = [], ""
    for word in str(text).split(" "):
        candidate = f"{line} {word}" if line else word
        if line and _width(candidate) > width:
            lines.append(line)
            line = indent + word
        else:
            line = candidate
    lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [_fit(" ".join(lines[max_lines - 1:]).replace(indent, " "), width)]
    return [_fit(l, width) for l in lines]


def _legend(scene, names_first=(), highlight=()):
    """Legend lines (text, rgb): the named items first, then the rest, each with its drawn colour."""
    order = [s for s in scene if s.name in names_first] + [s for s in scene if s.name not in names_first]
    lines = []
    for s in order:
        lit = highlighted(s.name, highlight)
        mark = " *" if s.name in names_first else ""
        text = f"{s.name} ({s.kind}){mark}{'' if lit else ' - placement only'}"
        lines.append((text, s.rgb if lit else _faded(s.rgb)))
    return lines


def _fit_lines(fixed, flexible, legend, capacity, more_text):
    """Fit fixed lines, then the legend's first `legend[0]` entries, then flexible lines, then the rest of the legend.

    legend = (n_required, lines). Lines are str or (str, rgb).
    """
    required, entries = legend
    fixed = fixed[:max(1, capacity - 2)]
    room = capacity - len(fixed) - min(required, len(entries)) - (1 if required < len(entries) else 0)
    if len(flexible) > room:
        flexible = flexible[:max(0, room - 1)] + [f"  +{len(flexible) - max(0, room - 1)} more lines {more_text}"]
    rest = capacity - len(fixed) - len(flexible) - 1  # the "Legend:" header
    if len(entries) > rest:
        entries = entries[:max(0, rest - 1)] + [f"  +{len(entries) - max(0, rest - 1)} more items"]
    return fixed + flexible + ["Legend:"] + entries


def _draw_lines(draw, origin, lines, width):
    x, y = origin
    for line in lines:
        if isinstance(line, tuple):
            text, rgb = line
            draw.rectangle((x + 2, y + 1, x + 2 + SWATCH, y + 1 + SWATCH), fill=tuple(rgb), outline=EDGE_RGB)
            render_model._text(draw, (x + SWATCH + 8, y), _fit(text, width - SWATCH - 8), FONT_SIZE)
        else:
            colour = {"Status: fail": FAIL_RGB, "Status: pass": PASS_RGB}.get(line, render_model.TEXT_RGB)
            render_model._text(draw, (x, y), line, FONT_SIZE, fill=colour)
        y += LINE_H


def _cross(render_, point):
    """Mark the check's location with a thin cross that leaves the point itself visible."""
    draw = ImageDraw.Draw(render_.image)
    (x, y), = render_.project([point])
    gap, arm = CROSS
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        draw.line((x + dx * gap, y + dy * gap, x + dx * arm, y + dy * arm), fill=MARKER_RGB, width=1)


def _clear_margins(render_):
    """Blank the panel outside its content box: zoomed geometry runs off the panel and would hide the annotations."""
    draw = ImageDraw.Draw(render_.image)
    w, h = render_.image.size
    x0, y0, x1, y1 = render_model.content_box((w, h))
    for box in ((0, 0, w, y0), (0, y1, w, h), (0, 0, x0, h), (x1, 0, w, h)):
        draw.rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=BACKGROUND)


def _frame(sheet, columns, rows, panel):
    draw = ImageDraw.Draw(sheet)
    w, h = sheet.size
    for c in range(1, columns):
        draw.line((c * panel, TITLE_H, c * panel, h), fill=GRID_RGB)
    for r in range(rows):
        draw.line((0, TITLE_H + r * panel, w, TITLE_H + r * panel), fill=GRID_RGB)


def _save(sheet, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    render_model.save_png(sheet, path)
    return path.resolve().as_posix()


# --- interface sheet ------------------------------------------------------------------------------------------------


def _check_text(result, width):
    """(fixed lines, measured/expected lines, item names) for an interface, target or overlap result."""
    name = result.get("name") or f"{result['a']} / {result['b']}"
    kind = result.get("type", "overlap")
    items = result.get("items") or [result["a"], result["b"]]
    location = result.get("location") or [None, None, None]
    fixed = [f"Check: {name} ({kind})", f"Status: {result['status']}"]
    fixed += _wrap(result.get("message", ""), width, 3)
    fixed += _wrap(f"Source: {result.get('source', 'overlap sweep: no two items may overlap')}", width, 2)
    fixed.append(_fit(f"Location: {_point(location)} (red cross, panel centre)", width))
    if "measured" in result:
        measured, expected = result["measured"], result["expected"]
    else:
        measured, expected = {"volume_mm3": result.get("volume_mm3")}, {"overlap_max": "touching only"}
    flexible = []
    for title, values in (("Measured", measured), ("Expected", expected)):
        if isinstance(values, dict):
            flexible.append(f"{title}:")
            for key, value in values.items():
                flexible += _wrap(f"  {key}: {_value(value)}", width, 2)
        else:
            flexible.append(_fit(f"{title}: {_value(values)}", width))
    return fixed, flexible, items


def interface_sheet(name, scene, check_result, path) -> str:
    """One sheet for one check: a section panel per plane in check_result["planes"] (at most 2), and an info panel.

    The panels share one scale and are centred on the check's location, showing a box ZOOM_MM wide (at least
    ZOOM_PER_DIAMETER x the largest fastener diameter among the check's items). A check with a single plane gets a
    second panel with the same cut magnified DETAIL times, so sub-millimetre features are visible. Every item of the
    scene is cut, each in its own colour; material of two items that overlaps is red. The info panel: status,
    message, source, location, measured and expected values, and a colour legend (the check's own items first,
    marked *). Writes the PNG and returns its posix path.
    """
    planes = list(check_result.get("planes") or [])[:MAX_PLANES]
    if not planes:
        raise ValueError(f"{name}: the check reports no planes to cut")
    location = check_result.get("location")
    if not location or any(v is None for v in location):
        location = planes[0][0]
    fixed, flexible, items = _check_text(check_result, IFACE_PANEL - 24)
    size = (IFACE_PANEL, IFACE_PANEL)
    x0, y0, x1, y1 = render_model.content_box(size)
    box = zoom_box_mm([s.diameter for s in scene if s.name in items])
    mm_per_px = box / min(x1 - x0, y1 - y0)
    meshes = [(s.mesh, s.rgb) for s in scene]
    cuts = [(f"Section {i + 1}: through {_point(p)}, normal {_point(n)}", p, n, mm_per_px)
            for i, (p, n) in enumerate(planes)]
    if len(planes) == 1:  # small features (a snap's engagement, a seat's gap) need a closer look
        point, normal = planes[0]
        cuts.append((f"Detail x{DETAIL}: the same section", point, normal, mm_per_px / DETAIL))
    panels = []
    for title, point, normal, scale in cuts:
        r = render_model.render_scene(meshes, section=Section(tuple(point), tuple(normal)), mm_per_px=scale,
                                      size=size, center=location)
        _clear_margins(r)
        _cross(r, location)
        panels.append(render_model.annotate(r, title))

    if any(r.overlap is not None and r.overlap.any() for r in panels):
        fixed.insert(2, "Red in a cut: material of two items overlaps")
    columns = len(panels) + 1
    sheet = Image.new("RGB", (columns * IFACE_PANEL, TITLE_H + IFACE_PANEL), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    status = check_result["status"]
    render_model._text(draw, (12, TITLE_H / 2), f"{name}  ·  {status.upper()}", 18, anchor="lm",
                       fill=FAIL_RGB if status == "fail" else PASS_RGB)
    for i, panel in enumerate(panels):
        sheet.paste(panel.image, (i * IFACE_PANEL, TITLE_H))
    capacity = (IFACE_PANEL - 2 * INFO_TOP) // LINE_H
    lines = _fit_lines(fixed, flexible, (len(items), _legend(scene, items)), capacity, "in assembly.result.json")
    _draw_lines(draw, (len(panels) * IFACE_PANEL + 12, TITLE_H + INFO_TOP), lines, IFACE_PANEL - 24)
    _frame(sheet, columns, 1, IFACE_PANEL)
    return _save(sheet, path)


# --- overview sheet -------------------------------------------------------------------------------------------------


def overview_sheet(title, scene, path, highlight=(), *, lines=(), markers=()) -> str:
    """Iso, front, right and top views of the whole scene (2 x 2) with an info column and a colour legend.

    highlight: item names or instance names ("c1") drawn in full colour; the others are faded (for a layout: the
    instances checked by placement only). Nothing highlighted draws everything in full colour. lines: extra info
    lines (status, counts, failures). markers: (point, label) pairs drawn on every view. Returns the posix path.
    """
    meshes = [(s.mesh, s.rgb if highlighted(s.name, highlight) else _faded(s.rgb)) for s in scene]
    size = (IFACE_PANEL, IFACE_PANEL)
    ortho = [View("front"), View("right"), View("top")]
    shared = render_model.fit_mm_per_px([m for m, _ in meshes], ortho, size)
    markers = list(markers)[:render_model.MAX_MARKERS]
    panels = [render_model.annotate(render_model.render_scene(meshes, View("iso"), size=size, markers=markers), "Iso")]
    for view, name in zip(ortho, ("Front (looking +Y)", "Right (looking -X)", "Top (looking -Z)")):
        r = render_model.render_scene(meshes, view, mm_per_px=shared, size=size, markers=markers)
        panels.append(render_model.annotate(r, name))

    sheet = Image.new("RGB", (3 * IFACE_PANEL, TITLE_H + 2 * IFACE_PANEL), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    render_model._text(draw, (12, TITLE_H / 2), title, 18, anchor="lm")
    for i, panel in enumerate(panels):
        row, col = divmod(i, 2)
        sheet.paste(panel.image, (col * IFACE_PANEL, TITLE_H + row * IFACE_PANEL))
    width = IFACE_PANEL - 24
    fixed = [l for line in lines for l in _wrap(line, width, 3)]
    if highlight:
        fixed.append(_fit("Faded items: checked by placement only", width))
    capacity = (2 * IFACE_PANEL - 2 * INFO_TOP) // LINE_H
    info = _fit_lines(fixed, [], (0, _legend(scene, highlight=highlight)), capacity, "")
    _draw_lines(draw, (2 * IFACE_PANEL + 12, TITLE_H + INFO_TOP), info, width)
    _frame(sheet, 3, 2, IFACE_PANEL)
    return _save(sheet, path)
