"""print3d preview renders: orthographic views and section cuts of a part's mesh, drawn with numpy and Pillow.

Pure rendering: takes trimesh meshes, never imports build123d (shapes are only asked to tessellate themselves),
and writes nothing except through save_png(). Deterministic: identical input gives byte-identical PNGs.
"""
from __future__ import annotations

import functools
import math
from dataclasses import dataclass

import numpy as np
import trimesh
from PIL import Image, ImageDraw, ImageFont

from run_model import ANGULAR_TOL  # same angular tolerance as the export and the checks

PREVIEW_LINEAR_TOL = 0.05  # mm, coarser than export (0.01) so the preview stays fast
PANEL = 390  # px, square panel side
TITLE_H = 40  # px, sheet title bar
SHEET_SIZE = (4 * PANEL, TITLE_H + 2 * PANEL)  # long edge <= 1568 so Claude's image input keeps full resolution
CONTENT = (15, 34, 15, 52)  # px margins inside a panel: left, top (title), right, bottom (triad and scale bar)
MAX_MARKERS = 10
SECTION_MARKER_MM = 2.0  # markers within this distance of a cut plane are shown on that section
SHARP_DEG = 30.0  # faces meeting at more than this angle draw an edge

BACKGROUND = (255, 255, 255)
PART_RGB = np.array([200, 204, 210], dtype=float)
CUT_RGB = (240, 140, 40)
EDGE_RGB = (40, 40, 40)
MARKER_RGB = (220, 30, 30)
AXIS_RGB = {"X": (210, 40, 40), "Y": (30, 150, 50), "Z": (40, 80, 220)}
TEXT_RGB = (20, 20, 20)
GRID_RGB = (210, 210, 210)
# Pillow's built-in font has no glyphs for these.
ASCII_TEXT = str.maketrans({"×": "x", "²": "2", "³": "3"})

NAMED_VIEWS = {  # name: (direction the viewer looks along, screen up)
    "front": ((0, 1, 0), (0, 0, 1)),
    "back": ((0, -1, 0), (0, 0, 1)),
    "right": ((-1, 0, 0), (0, 0, 1)),
    "left": ((1, 0, 0), (0, 0, 1)),
    "top": ((0, 0, -1), (0, 1, 0)),
    "bottom": ((0, 0, 1), (0, -1, 0)),
    "iso": ((-1, 1, -1), (0, 0, 1)),  # from the front-right-top corner
}


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("direction must not be zero")
    return v / n


@dataclass(frozen=True)
class View:
    """An orthographic view: a named view, or a free `direction` (where the viewer looks) with an optional `up`."""

    name: str | None = None
    direction: tuple | None = None
    up: tuple | None = None

    def basis(self):
        """(right, up, forward) unit vectors. Screen x = p·right, screen y = p·up, depth = p·forward."""
        if self.name is not None:
            if self.name not in NAMED_VIEWS:
                raise ValueError(f"Unknown view {self.name!r}; choose from {', '.join(NAMED_VIEWS)}")
            direction, up = NAMED_VIEWS[self.name]
        elif self.direction is None:
            raise ValueError("View needs a name or a direction")
        else:
            direction, up = self.direction, self.up
        forward = _unit(direction)
        if up is None:
            up = (0, 1, 0) if abs(forward[2]) > 0.999 else (0, 0, 1)
        up = np.asarray(up, dtype=float)
        up = _unit(up - up.dot(forward) * forward)
        return np.cross(forward, up), up, forward


@dataclass(frozen=True)
class Section:
    """A cut plane through `point` (mm). Material on the side `normal` points to is removed; viewed along -normal."""

    point: tuple
    normal: tuple

    def view(self):
        return View(direction=tuple(-_unit(self.normal)))


@dataclass
class Render:
    image: Image.Image
    mm_per_px: float
    basis: tuple  # (right, up, forward)
    center: tuple  # screen (x, y) in mm drawn at origin_px
    origin_px: tuple  # pixel position of `center`: the content box centre
    depth: np.ndarray | None = None  # (H, W) depth buffer in mm along forward, inf where empty

    def project(self, points_mm):
        """(N, 3) model points (mm) -> (N, 2) pixel coordinates (x right, y down)."""
        right, up, _ = self.basis
        pts = np.atleast_2d(np.asarray(points_mm, dtype=float))
        x = (pts @ right - self.center[0]) / self.mm_per_px + self.origin_px[0]
        y = self.origin_px[1] - (pts @ up - self.center[1]) / self.mm_per_px
        return np.stack([x, y], axis=1)


def preview_mesh(shape):
    """Tessellate a build123d shape at preview resolution."""
    vertices, triangles = shape.tessellate(PREVIEW_LINEAR_TOL, ANGULAR_TOL)
    return trimesh.Trimesh(
        vertices=[(v.X, v.Y, v.Z) for v in vertices], faces=triangles, process=False, validate=False
    )


def _corners(mesh):
    """The 8 bbox corners, or the origin for an empty mesh."""
    return trimesh.bounds.corners(mesh.bounds) if len(mesh.vertices) else np.zeros((1, 3))


def content_box(size):
    w, h = size
    left, top, right, bottom = CONTENT
    return left, top, w - right, h - bottom


def fit_mm_per_px(mesh, views, size):
    """The smallest mm/px at which the mesh's bbox fits the content box in every view (one shared scale)."""
    x0, y0, x1, y1 = content_box(size)
    corners = _corners(mesh)
    scale = 0.0
    for view in views:
        right, up, _ = view.basis()
        scale = max(scale, np.ptp(corners @ right) / (x1 - x0), np.ptp(corners @ up) / (y1 - y0))
    return float(scale) or 1.0


def _rasterize(xy, z, width, height, keep=None):
    """Z-buffer triangles (xy: N x 3 x 2 px, z: N x 3 depth). Pixels nearer than `keep` are clipped away.

    Returns (depth (H, W), face index (H, W) with -1 where empty). Triangles are bucketed by their power-of-two
    pixel bbox, so every bucket is one dense numpy batch and nothing loops over pixels in Python.
    """
    depth = np.full(height * width, np.inf)
    face = np.full(height * width, -1, dtype=np.int64)
    if len(xy) == 0:
        return depth.reshape(height, width), face.reshape(height, width)
    limit = np.array([width - 1, height - 1])
    lo = np.clip(np.floor(xy.min(axis=1) - 0.5).astype(np.int64), 0, limit)
    hi = np.clip(np.ceil(xy.max(axis=1) - 0.5).astype(np.int64), 0, limit)
    a, b, c = xy[:, 0], xy[:, 1], xy[:, 2]
    area = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])
    span = hi - lo + 1
    onscreen = (xy.max(axis=1) >= 0).all(axis=1) & (xy.min(axis=1) <= limit + 1).all(axis=1)
    valid = (np.abs(area) > 1e-12) & onscreen
    bw = 1 << np.ceil(np.log2(np.maximum(span[:, 0], 1))).astype(np.int64)
    bh = 1 << np.ceil(np.log2(np.maximum(span[:, 1], 1))).astype(np.int64)
    pix_all, z_all, f_all = [], [], []
    for w, h in sorted(set(zip(bw[valid].tolist(), bh[valid].tolist()))):
        idx = np.nonzero(valid & (bw == w) & (bh == h))[0]
        gx, gy = (g.ravel()[None, :] for g in np.meshgrid(np.arange(w), np.arange(h)))
        for chunk in np.array_split(idx, len(idx) * w * h // 2_000_000 + 1):  # bound memory per batch
            px, py = lo[chunk, 0, None] + gx, lo[chunk, 1, None] + gy
            sx, sy = px + 0.5, py + 0.5  # sample at pixel centres
            ta, tb, tc = a[chunk], b[chunk], c[chunk]
            inv = 1.0 / area[chunk, None]
            w0 = ((tb[:, 0, None] - sx) * (tc[:, 1, None] - sy) - (tb[:, 1, None] - sy) * (tc[:, 0, None] - sx)) * inv
            w1 = ((tc[:, 0, None] - sx) * (ta[:, 1, None] - sy) - (tc[:, 1, None] - sy) * (ta[:, 0, None] - sx)) * inv
            w2 = 1.0 - w0 - w1
            inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
            inside &= (px <= hi[chunk, 0, None]) & (py <= hi[chunk, 1, None])
            zc = z[chunk]
            pz = w0 * zc[:, 0, None] + w1 * zc[:, 1, None] + w2 * zc[:, 2, None]
            if keep is not None:
                inside &= pz >= keep
            rows, cols = np.nonzero(inside)
            pix_all.append(py[rows, cols] * width + px[rows, cols])
            z_all.append(pz[rows, cols])
            f_all.append(chunk[rows])
    if pix_all:
        pix, zz, ff = np.concatenate(pix_all), np.concatenate(z_all), np.concatenate(f_all)
        order = np.lexsort((ff, zz, pix))  # per pixel: nearest first; face index breaks ties deterministically
        pix, zz, ff = pix[order], zz[order], ff[order]
        first = np.ones(len(pix), dtype=bool)
        first[1:] = pix[1:] != pix[:-1]
        depth[pix[first]] = zz[first]
        face[pix[first]] = ff[first]
    return depth.reshape(height, width), face.reshape(height, width)


def _edges(depth, face, normals, cut, mm_per_px):
    """Pixels on silhouettes, depth jumps, sharp creases and cut outlines."""
    filled = face >= 0
    n = np.where(filled[..., None], normals[np.maximum(face, 0)], 0.0)
    z = np.where(filled, depth, 0.0)
    cos_sharp = math.cos(math.radians(SHARP_DEG))
    edge = np.zeros(face.shape, dtype=bool)
    for a, b in (((slice(None, -1), slice(None)), (slice(1, None), slice(None))),
                 ((slice(None), slice(None, -1)), (slice(None), slice(1, None)))):
        both = filled[a] & filled[b]
        jump = filled[a] != filled[b]
        jump |= both & (np.abs(z[a] - z[b]) > 4 * mm_per_px)
        jump |= both & ((n[a] * n[b]).sum(axis=-1) < cos_sharp)
        jump |= both & (cut[a] != cut[b])
        edge[a] |= jump & filled[a]
        edge[b] |= jump & filled[b]
    return edge


def render(mesh, view=None, *, section=None, mm_per_px=None, size=(PANEL, PANEL), markers=()):
    """Render one orthographic view of `mesh`, or a section viewed square-on. Returns a Render.

    markers: (point_mm, label) pairs, drawn filled when visible and hollow when hidden behind the part.
    """
    if section is not None:
        view = section.view()
    elif view is None:
        raise ValueError("render() needs a view or a section")
    right, up, forward = view.basis()
    width, height = size
    x0, y0, x1, y1 = content_box(size)
    if mm_per_px is None:
        mm_per_px = fit_mm_per_px(mesh, [view], size)
    corners = _corners(mesh)
    sx, sy = corners @ right, corners @ up
    center = (float(sx.min() + np.ptp(sx) / 2), float(sy.min() + np.ptp(sy) / 2))
    result = Render(None, float(mm_per_px), (right, up, forward), center, ((x0 + x1) / 2, (y0 + y1) / 2))

    triangles = mesh.triangles
    xy = result.project(triangles.reshape(-1, 3)).reshape(-1, 3, 2)
    keep = None if section is None else float(np.asarray(section.point, dtype=float) @ forward)
    depth, face = _rasterize(xy, triangles @ forward, width, height, keep)
    result.depth = depth
    if len(mesh.faces) == 0:  # nothing to draw: a blank panel (markers still drawn)
        result.image = Image.new("RGB", size, BACKGROUND)
        if markers:
            draw_markers(result, markers)
        return result

    normals = mesh.face_normals
    facing = normals @ forward  # < 0: the face looks toward the viewer
    filled = face >= 0
    back = filled & (facing[np.maximum(face, 0)] > 0)
    # With the near half clipped away, a visible back face is the inside of the solid: that pixel is cut material.
    cut = back if section is not None else np.zeros_like(filled)
    light = _unit(-forward + 0.6 * up - 0.4 * right)  # from the viewer's upper left
    shade = np.where(facing > 0, 0.35, 0.45 + 0.55 * np.clip(normals @ light, 0, 1))  # back faces of open meshes: dark
    pixels = np.full((height, width, 3), BACKGROUND, dtype=float)
    pixels[filled] = PART_RGB * shade[face[filled], None]
    pixels[cut] = CUT_RGB
    pixels[_edges(depth, face, normals, cut, mm_per_px)] = EDGE_RGB
    result.image = Image.fromarray(pixels.round().astype(np.uint8), "RGB")
    if markers:
        draw_markers(result, markers)
    return result


@functools.lru_cache(maxsize=None)
def font(size=13):
    return ImageFont.load_default(size=size)


def _text(draw, xy, text, size, fill=TEXT_RGB, anchor=None):
    draw.text(xy, str(text).translate(ASCII_TEXT), fill=fill, font=font(size), anchor=anchor)


def draw_markers(render_, markers):
    """Draw (point_mm, label) markers at render_.project(point)."""
    draw = ImageDraw.Draw(render_.image)
    _, _, forward = render_.basis
    h, w = render_.depth.shape
    tolerance = max(0.5, 3 * render_.mm_per_px)  # mm in depth: a marker on the visible surface is not hidden
    for point, label in markers:
        (x, y), = render_.project([point])
        col, row = math.floor(x), math.floor(y)
        hidden = 0 <= row < h and 0 <= col < w and \
            float(np.asarray(point, dtype=float) @ forward) > render_.depth[row, col] + tolerance
        box = (x - 7, y - 7, x + 7, y + 7)
        if hidden:
            draw.ellipse(box, outline=MARKER_RGB, width=2)
            _text(draw, (x, y), label, 11, fill=MARKER_RGB, anchor="mm")
        else:
            draw.ellipse(box, fill=MARKER_RGB)
            _text(draw, (x, y), label, 11, fill=(255, 255, 255), anchor="mm")


def scale_bar_mm(mm_per_px, width_px):
    """The largest 1, 2 or 5 x 10^n mm that is at most 30 % of the panel width."""
    limit = 0.3 * width_px * mm_per_px
    exponent = math.floor(math.log10(limit))
    for step in (5, 2, 1):
        length = float(f"{step * 10.0 ** exponent:.6g}")
        if length <= limit * (1 + 1e-9):
            return length
    return float(f"{10.0 ** exponent:.6g}")  # unreachable: 1 x 10^floor(log10(limit)) <= limit


def annotate(render_, title):
    """Panel title (top left), projected XYZ axis triad (bottom left) and scale bar (bottom right)."""
    draw = ImageDraw.Draw(render_.image)
    width, height = render_.image.size
    _text(draw, (10, 8), title, 14)
    right, up, _ = render_.basis
    ox, oy = 28, height - 26
    for axis, vec in zip("XYZ", np.eye(3)):
        dx, dy = float(vec @ right), -float(vec @ up)
        if math.hypot(dx, dy) < 0.2:  # the axis points along the view: draw a dot
            draw.ellipse((ox - 3, oy - 3, ox + 3, oy + 3), fill=AXIS_RGB[axis])
            continue
        draw.line((ox, oy, ox + 20 * dx, oy + 20 * dy), fill=AXIS_RGB[axis], width=2)
        _text(draw, (ox + 28 * dx, oy + 28 * dy), axis, 12, fill=AXIS_RGB[axis], anchor="mm")
    length = scale_bar_mm(render_.mm_per_px, width)
    px = length / render_.mm_per_px
    x1, y = width - 15, height - 22
    draw.line((x1 - px, y, x1, y), fill=TEXT_RGB, width=3)
    for x in (x1 - px, x1):
        draw.line((x, y - 5, x, y + 5), fill=TEXT_RGB, width=1)
    _text(draw, (x1 - px / 2, y - 8), f"{length:g} mm", 12, anchor="mb")
    return render_
