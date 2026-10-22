"""Unit tests for render_model: projection, z-buffer, section cut faces, markers, scale bar and the sheet."""
import io

import numpy as np
import pytest
import trimesh

import render_model
from render_model import CUT_RGB, MARKER_RGB, PANEL, Section, View, fit_mm_per_px, render, scale_bar_mm


def box(extents, center):
    return trimesh.creation.box(extents=extents, transform=trimesh.transformations.translation_matrix(center))


def cup(length=40.0, width=30.0, height=20.0, wall=2.0, floor=3.0):
    """Open-top cup on the bed (2 mm walls, 3 mm floor), as one closed mesh from build123d."""
    from build123d import Align, Box, BuildPart, Locations, Mode

    bed = (Align.CENTER, Align.CENTER, Align.MIN)
    with BuildPart() as part:
        Box(length, width, height, align=bed)
        with Locations((0, 0, floor)):
            Box(length - 2 * wall, width - 2 * wall, height, align=bed, mode=Mode.SUBTRACT)
    return render_model.preview_mesh(part.part)


def pixels(r):
    return np.asarray(r.image)


def color_mask(r, rgb):
    return np.all(pixels(r) == np.array(rgb, dtype=np.uint8), axis=-1)


# --- views and projection -----------------------------------------------------------------------

@pytest.mark.parametrize(
    "name, right, up",
    [
        ("front", (1, 0, 0), (0, 0, 1)),
        ("back", (-1, 0, 0), (0, 0, 1)),
        ("right", (0, 1, 0), (0, 0, 1)),
        ("left", (0, -1, 0), (0, 0, 1)),
        ("top", (1, 0, 0), (0, 1, 0)),
        ("bottom", (1, 0, 0), (0, -1, 0)),
    ],
)
def test_named_view_axes(name, right, up):
    r, u, _ = View(name).basis()
    assert r == pytest.approx(right) and u == pytest.approx(up)


def test_iso_sees_front_right_and_top():
    right, up, forward = View("iso").basis()
    assert forward[1] > 0 and forward[0] < 0 and forward[2] < 0  # looks toward +Y, -X, -Z
    assert up[2] > 0 and right[0] > 0 and right[1] > 0


def test_unknown_view_and_zero_direction_raise():
    with pytest.raises(ValueError, match="Unknown view 'side'"):
        View("side").basis()
    with pytest.raises(ValueError, match="must not be zero"):
        View(direction=(0, 0, 0)).basis()


def test_free_direction_along_z_uses_y_up():
    _, up, _ = View(direction=(0, 0, -1)).basis()
    assert up == pytest.approx((0, 1, 0))


def test_project_front_maps_x_right_and_z_up():
    r = render(box((40, 30, 20), (0, 0, 10)), View("front"))
    (x0, y0), (x1, y1) = r.project([(-20, 0, 0), (20, 0, 20)])
    assert x1 > x0 and y1 < y0  # +X right, +Z up (pixel y grows downward)
    assert (x1 - x0) * r.mm_per_px == pytest.approx(40)
    assert (y0 - y1) * r.mm_per_px == pytest.approx(20)


def test_part_fills_the_projected_rectangle():
    r = render(box((40, 30, 20), (0, 0, 10)), View("front"))
    (x0, y0), (x1, y1) = r.project([(-20, 0, 20), (20, 0, 0)])
    filled = np.isfinite(r.depth)
    cols = np.nonzero(filled.any(axis=0))[0]
    rows = np.nonzero(filled.any(axis=1))[0]
    assert cols[0] == pytest.approx(x0, abs=1) and cols[-1] == pytest.approx(x1, abs=1)
    assert rows[0] == pytest.approx(y0, abs=1) and rows[-1] == pytest.approx(y1, abs=1)


def test_shared_scale_fits_every_ortho_view():
    mesh = box((100, 20, 10), (0, 0, 5))
    views = [View("front"), View("right"), View("top")]
    shared = fit_mm_per_px(mesh, views, (PANEL, PANEL))
    assert shared == pytest.approx(max(fit_mm_per_px(mesh, [v], (PANEL, PANEL)) for v in views))


# --- z-buffer -----------------------------------------------------------------------------------

def test_nearer_box_hides_the_one_behind():
    near = box((10, 10, 10), (0, -20, 5))  # front view looks along +Y, so smaller y is nearer
    far = box((30, 10, 30), (0, 20, 15))
    mesh = trimesh.util.concatenate([near, far])
    r = render(mesh, View("front"))
    (cx, cy), = r.project([(0, 0, 5)])
    assert r.depth[int(cy), int(cx)] == pytest.approx(-25, abs=1e-6)  # near box's front face at y = -25
    order_swapped = render(trimesh.util.concatenate([far, near]), View("front"))
    assert order_swapped.depth[int(cy), int(cx)] == pytest.approx(-25, abs=1e-6)  # not painter's order


# --- sections -----------------------------------------------------------------------------------

def test_cup_section_paints_walls_and_floor_but_not_the_cavity():
    mesh = cup()
    r = render(mesh, section=Section((0, 0, 10), (0, -1, 0)))  # cut at y = 0, viewed from the front
    cut = color_mask(r, CUT_RGB)
    (wall_x, wall_y), (cav_x, cav_y), (floor_x, floor_y) = r.project([(-19, 0, 10), (0, 0, 10), (0, 0, 1.5)])
    assert cut[int(wall_y), int(wall_x)]
    assert cut[int(floor_y), int(floor_x)]
    assert not cut[int(cav_y), int(cav_x)]  # the cavity shows the far inner wall, not cut material
    assert np.isfinite(r.depth[int(cav_y), int(cav_x)])


def test_section_through_solid_is_cut_everywhere_inside():
    r = render(box((40, 30, 20), (0, 0, 10)), section=Section((0, 0, 10), (0, 0, 1)))
    (x, y), = r.project([(0, 0, 10)])
    cut = color_mask(r, CUT_RGB)
    assert cut[int(y) - 50:int(y) + 50, int(x) - 50:int(x) + 50].all()


def test_section_beyond_the_part_has_no_cut_face():
    mesh = box((40, 30, 20), (0, 0, 10))
    r = render(mesh, section=Section((0, 0, 30), (0, 0, 1)), mm_per_px=fit_mm_per_px(mesh, [View("top")], (PANEL, PANEL)))
    assert not color_mask(r, CUT_RGB).any()
    gone = render(mesh, section=Section((0, 0, -5), (0, 0, 1)))  # everything removed
    assert not np.isfinite(gone.depth).any()


def test_empty_mesh_renders_a_blank_panel():
    r = render(trimesh.Trimesh(), View("front"))
    assert r.image.size == (PANEL, PANEL) and not np.isfinite(r.depth).any()


# --- markers and annotations --------------------------------------------------------------------

def test_visible_marker_is_filled_at_its_projection_and_hidden_marker_is_hollow():
    mesh = box((40, 30, 20), (0, 0, 10))
    front_point, back_point = (-10, -15, 10), (10, 15, 10)  # on the front face, and on the hidden back face
    r = render(mesh, View("front"), markers=[(front_point, 1), (back_point, 2)])
    red = color_mask(r, MARKER_RGB)
    (fx, fy), (bx, by) = r.project([front_point, back_point])
    assert red[int(fy) + 4, int(fx) + 4]  # filled disc: red off-centre
    assert not red[int(by) + 2, int(bx) + 2]  # hollow ring: no red just off-centre
    assert red[int(by), int(bx) + 6] or red[int(by), int(bx) + 7]  # but red on the ring


@pytest.mark.parametrize("size_mm", [2, 7, 20, 60, 250])
def test_scale_bar_is_round_and_at_most_30_percent(size_mm):
    mm_per_px = fit_mm_per_px(box((size_mm,) * 3, (0, 0, size_mm / 2)), [View("front")], (PANEL, PANEL))
    length = scale_bar_mm(mm_per_px, PANEL)
    mantissa = length / 10 ** np.floor(np.log10(length))
    assert round(mantissa, 6) in (1, 2, 5)
    assert length / mm_per_px <= 0.3 * PANEL + 1e-6
    assert length / mm_per_px > 0.3 * PANEL / 2.5  # the largest round value, not a tiny one


# --- check regions and the sheet ----------------------------------------------------------------

from render_model import SHEET_SIZE, check_regions, info_lines, preview_sheet  # noqa: E402

REPORT = {
    "status": "fail",
    "results": [
        {"check": "sealed", "status": "pass", "message": "ok"},
        {"check": "wall", "status": "fail", "message": "Wall 0.50 mm < 0.8 mm in 1 region(s); thicken it",
         "regions": [{"center": [0.0, -14.75, 7.5], "value": 0.5}]},
        {"check": "thin", "status": "pass", "message": "ok"},
        {"check": "overhang", "status": "warn", "message": "overhang",
         "regions": [{"center": [1.0, 2.0, 3.0], "area_mm2": 21.04, "max_angle_deg": 90.0}]},
    ],
}


def test_check_regions_lists_fail_and_warn_regions_in_check_order():
    regions = check_regions(REPORT)
    assert [r[0] for r in regions] == ["wall", "overhang"]
    assert regions[0][1] == "0.50 mm thick at (0.0, -14.8, 7.5)"
    assert regions[1][1] == "21.0 mm2 at 90° at (1.0, 2.0, 3.0)"
    assert regions[0][2] == (0.0, -14.75, 7.5)
    assert check_regions(None) == []


INFO = {"bbox": (40.0, 30.0, 20.0), "volume": 8088.0, "profile": "centauri-carbon-pla-plus",
        "overrides": {"length": 40.0}, "checks": "pass", "sealed": True}


def test_info_lines_cap_overrides_and_markers():
    lines = info_lines("main", {**INFO, "overrides": {f"p{i}": i for i in range(11)}}, [])
    assert "  +3 more" in lines
    assert sum(line.startswith("  p") for line in lines) == 8
    regions = [("wall", f"region {i}", (0, 0, 0)) for i in range(13)]
    lines = info_lines("main", INFO, regions)
    assert sum(" wall: " in line for line in lines) == 10
    assert lines[-1] == "  +3 more regions not marked"


def test_info_panel_fits_the_sheet_in_the_worst_case():
    # Review finding: 8 overrides + 10 markers + their headers overflowed the panel and cut off "+N more".
    info = {**INFO, "overrides": {f"p{i}": i for i in range(12)}}
    regions = [("wall", f"region {i}", (0, 0, 0)) for i in range(15)]
    lines = info_lines("main", info, regions)
    assert len(lines) <= render_model.INFO_MAX_LINES
    assert render_model.INFO_TOP + render_model.INFO_MAX_LINES * render_model.INFO_LINE_H <= PANEL
    assert lines[-1] == "  +5 more regions not marked"
    assert sum(" wall: " in line for line in lines) == 10  # markers keep priority over overrides
    assert "Overrides:" in lines and any(line.startswith("  +") and line.endswith(" more") for line in lines)


def test_sheet_size_and_determinism():
    mesh = cup()
    sheet = preview_sheet("main", mesh, INFO, check_regions(REPORT))
    assert sheet.size == SHEET_SIZE and max(SHEET_SIZE) <= 1568
    again = preview_sheet("main", mesh, INFO, check_regions(REPORT))
    a, b = io.BytesIO(), io.BytesIO()
    sheet.save(a, format="PNG")
    again.save(b, format="PNG")
    assert a.getvalue() == b.getvalue()


def test_sheet_has_cut_faces_only_in_the_section_row():
    sheet = np.asarray(preview_sheet("main", cup(), INFO))
    cut = np.all(sheet == np.array(CUT_RGB, dtype=np.uint8), axis=-1)
    top_row = cut[render_model.TITLE_H:render_model.TITLE_H + PANEL]
    section_row = cut[render_model.TITLE_H + PANEL:, : 3 * PANEL]
    assert not top_row.any()
    for col in range(3):
        assert section_row[:, col * PANEL:(col + 1) * PANEL].any(), f"section panel {col} has no cut face"


def test_unsealed_mesh_still_renders():
    open_box = box((40, 30, 20), (0, 0, 10))
    open_box.update_faces(np.arange(len(open_box.faces)) != 0)  # drop one triangle
    sheet = preview_sheet("main", open_box, {**INFO, "sealed": False})
    assert sheet.size == SHEET_SIZE


# --- cut-face artefacts (review findings) -------------------------------------------------------

from render_model import EDGE_RGB  # noqa: E402


def _interior(mask, margin=2):
    """`mask` shrunk by `margin` pixels on every side."""
    inner = mask.copy()
    for _ in range(margin):
        inner[1:, :] &= inner[:-1, :].copy()
        inner[:-1, :] &= inner[1:, :].copy()
        inner[:, 1:] &= inner[:, :-1].copy()
        inner[:, :-1] &= inner[:, 1:].copy()
    return inner


def test_hidden_features_behind_the_cut_draw_no_lines_inside_the_cut_face():
    from build123d import Align, Box, BuildPart, Hole, Locations

    with BuildPart() as part:
        Box(40, 40, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((-10, 0, 20)):
            Hole(2, depth=10)  # blind hole on the kept side, behind the x = 0 cut
    mesh = render_model.preview_mesh(part.part)
    r = render(mesh, section=Section((0, 0, 10), (1, 0, 0)))
    cut = color_mask(r, CUT_RGB)
    inside = _interior(cut | color_mask(r, EDGE_RGB))
    assert cut.sum() > 1000
    assert not (color_mask(r, EDGE_RGB) & inside).any()


def test_face_on_the_cut_plane_renders_without_speckle():
    from build123d import Align, Box, BuildPart, Locations

    with BuildPart() as part:
        Box(40, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((10, 0, 10)):
            Box(20, 40, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))  # step on the +X half: centre z = 10
    mesh = render_model.preview_mesh(part.part)
    (_, section, _), = [s for s in render_model.section_planes(mesh) if s[2] == 2]
    assert section.point[2] == pytest.approx(10)
    r = render(mesh, section=section)
    (lx0, ly0), (lx1, ly1) = r.project([(-19, 19, 10), (-1, -19, 10)])  # the base's top face, exactly on the cut
    left = (slice(int(ly0), int(ly1)), slice(int(lx0), int(lx1)))
    assert not color_mask(r, CUT_RGB)[left].any() and not color_mask(r, EDGE_RGB)[left].any()
    (rx0, ry0), (rx1, ry1) = r.project([(1, 19, 10), (19, -19, 10)])  # the step, cut through
    assert color_mask(r, CUT_RGB)[int(ry0):int(ry1), int(rx0):int(rx1)].all()


# --- scenes: several meshes, each in its own colour ---------------------------------------------

RED, BLUE = (220, 40, 40), (40, 40, 220)


def _hue_masks(r):
    px = pixels(r).astype(int)
    red = (px[..., 0] > 120) & (px[..., 2] < 90) & (px[..., 1] < 90)
    blue = (px[..., 2] > 120) & (px[..., 0] < 90) & (px[..., 1] < 90)
    return red, blue


def test_render_scene_two_colours():
    left, right = box((10, 10, 10), (0, 0, 5)), box((10, 10, 10), (10, 0, 5))  # touching at x = 5
    r = render_model.render_scene([(left, RED), (right, BLUE)], View("front"))
    red, blue = _hue_masks(r)
    assert red.sum() > 1000 and blue.sum() > 1000
    (lx, ly), (rx, ry) = r.project([(0, -5, 5), (10, -5, 5)])
    assert red[int(ly), int(lx)] and blue[int(ry), int(rx)]
    # A section draws each item's cut face in a darker shade of its own colour, with a line between them.
    s = render_model.render_scene([(left, RED), (right, BLUE)], section=Section((5, 0, 5), (0, -1, 0)))
    dark_red = tuple(round(v * render_model.SCENE_CUT_SHADE) for v in RED)
    dark_blue = tuple(round(v * render_model.SCENE_CUT_SHADE) for v in BLUE)
    (lx, ly), (rx, ry), (mx, my) = s.project([(0, 0, 5), (10, 0, 5), (5, 0, 5)])
    assert tuple(pixels(s)[int(ly), int(lx)]) == dark_red
    assert tuple(pixels(s)[int(ry), int(rx)]) == dark_blue
    assert color_mask(s, render_model.EDGE_RGB)[int(my) - 20:int(my) + 20, int(mx) - 2:int(mx) + 2].any()


def test_render_unchanged_for_single_mesh():
    mesh = cup()
    for kwargs in ({"view": View("iso")}, {"view": View("front"), "markers": [((0, -15, 10), 1)]}):
        a = render(mesh, **kwargs)
        b = render_model.render_scene([(mesh, render_model.PART_RGB)], **kwargs)
        assert a.image.tobytes() == b.image.tobytes()
    cut = render(mesh, section=Section((0, 0, 10), (0, -1, 0)))
    assert color_mask(cut, CUT_RGB).sum() > 1000  # render() keeps the orange cut faces


def test_render_scene_centre_and_scale():
    mesh = box((40, 30, 20), (0, 0, 10))
    r = render_model.render_scene([(mesh, RED)], View("front"), mm_per_px=0.1, center=(15, 0, 5))
    (x, y), = r.project([(15, 0, 5)])
    assert (x, y) == pytest.approx(r.origin_px)
    assert r.mm_per_px == 0.1


def test_render_scene_overlap_is_red_and_coincident_faces_dont_speckle():
    big, small = box((10, 10, 10), (0, 0, 5)), box((4, 10, 4), (0, 0, 5))  # small inside big, front faces flush
    r = render_model.render_scene([(big, RED), (small, BLUE)], View("front"))
    (x0, y0), (x1, y1) = r.project([(-1.5, -5, 6.5), (1.5, -5, 3.5)])
    patch = pixels(r)[int(y0):int(y1), int(x0):int(x1)].reshape(-1, 3)
    assert len({tuple(p) for p in patch.tolist()}) == 1  # one flat colour: the first item's, no z-fighting
    red, blue = _hue_masks(r)
    assert red[int(y0), int(x0)] and not blue.any()
    s = render_model.render_scene([(big, RED), (small, BLUE)], section=Section((0, 0, 5), (0, -1, 0)))
    (cx, cy), (ex, ey) = s.project([(0, 0, 5), (4, 0, 5)])
    assert tuple(pixels(s)[int(cy), int(cx)]) == render_model.OVERLAP_RGB and s.overlap[int(cy), int(cx)]
    assert tuple(pixels(s)[int(ey), int(ex)]) == tuple(round(v * render_model.SCENE_CUT_SHADE) for v in RED)
