"""Evidence sheets (assembly_sheets): item colours, interface sheets and overview sheets, on the boxlid fixture."""
import types

import numpy as np
import pytest
from PIL import Image

import assembly_checks as ac
import assembly_sheets
import render_model
from test_assembly_fasteners import interface, load, stand_in


@pytest.fixture(scope="module")
def boxlid():
    unit, items, _ = load()
    return unit, items, assembly_sheets.make_scene(items)


def _dark(rgb):
    return tuple(round(v * render_model.SCENE_CUT_SHADE) for v in rgb)


def test_item_colours():
    def item(name, kind, color=None):
        return types.SimpleNamespace(name=name, kind=kind, color=color)

    items = {i.name: i for i in [item("a", "part", (1, 2, 3)), item("b", "part"), item("s", "screw"),
                                 item("c", "standin"), item("n", "nut", (9, 9, 9))]}
    colours = assembly_sheets.item_colors(items)
    assert colours == {"a": (1, 2, 3), "b": assembly_sheets.PALETTE[0], "s": assembly_sheets.HARDWARE_RGB,
                       "c": assembly_sheets.PALETTE[1], "n": (9, 9, 9)}
    assert len(set(assembly_sheets.PALETTE)) == 8


def test_scene_uses_declared_colours(boxlid):
    _, _, scene = boxlid
    by_name = {s.name: s for s in scene}
    assert by_name["lid"].rgb == (90, 140, 220)
    assert by_name["screw-left"].rgb == assembly_sheets.HARDWARE_RGB
    assert by_name["screw-left"].diameter == 3.0 and by_name["lid"].diameter is None


def test_interface_sheet_written(boxlid, tmp_path):
    unit, items, scene = boxlid
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    first = assembly_sheets.interface_sheet("box / bolt-left", scene, result, tmp_path / "a" / "bolt-left.png")
    assembly_sheets.interface_sheet("box / bolt-left", scene, result, tmp_path / "b.png")
    assert first == (tmp_path / "a" / "bolt-left.png").resolve().as_posix()
    with Image.open(first) as image:
        assert max(image.size) <= 1568
        assert image.size[0] > image.size[1]  # two section panels and the info panel side by side
    assert (tmp_path / "a" / "bolt-left.png").read_bytes() == (tmp_path / "b.png").read_bytes()


def test_interface_sheet_cuts_every_item_of_the_bolt(boxlid, tmp_path):
    unit, items, scene = boxlid
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    path = assembly_sheets.interface_sheet("box / bolt-left", scene, result, tmp_path / "bolt.png")
    with Image.open(path) as image:
        panel = image.convert("RGB").crop((0, assembly_sheets.TITLE_H, assembly_sheets.IFACE_PANEL, image.size[1]))
        colours = {tuple(c) for c in np.asarray(panel).reshape(-1, 3).tolist()}
    # the first plane runs along the bolt: lid, base, screw and nut are all cut, each in its own colour
    assert {_dark((90, 140, 220)), _dark((200, 200, 200)), _dark(assembly_sheets.HARDWARE_RGB)} <= colours


def test_zoom_box():
    assert assembly_sheets.zoom_box_mm([]) == 40.0
    assert assembly_sheets.zoom_box_mm([3.0]) == 40.0
    assert assembly_sheets.zoom_box_mm([3.0, 12.0]) == 48.0


def test_overview_sheet(boxlid, tmp_path):
    _, _, scene = boxlid
    path = assembly_sheets.overview_sheet("box", scene, tmp_path / "overview.png")
    faded = assembly_sheets.overview_sheet("box", scene, tmp_path / "faded.png", highlight=("lid",))
    with Image.open(path) as image:
        assert max(image.size) <= 1568
    assert (tmp_path / "overview.png").read_bytes() != (tmp_path / "faded.png").read_bytes()
    assert faded.endswith("faded.png")


def test_highlight_matches_items_and_instances():
    assert assembly_sheets.highlighted("c1.block", ("c1", "strip"))
    assert assembly_sheets.highlighted("strip", ("c1", "strip"))
    assert not assembly_sheets.highlighted("c3.block", ("c1", "strip"))
    assert assembly_sheets.highlighted("c3.block", ())


def _png(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def _mask(pixels, rgb):
    return np.all(pixels == np.array(rgb, dtype=np.uint8), axis=-1)


def test_thin_interference_is_red_on_its_sheet(tmp_path):
    from build123d import Box, Pos

    items = {"a": stand_in("a", Box(10, 10, 10)), "b": stand_in("b", Pos(9.8, 0, 0) * Box(10, 10, 10))}
    entry, = ac.overlap_sweep(items, set())
    assert entry["size"] == pytest.approx([0.2, 10, 10], abs=1e-3)
    path = assembly_sheets.interface_sheet("t / overlap", assembly_sheets.make_scene(items), entry, tmp_path / "o.png")
    red = _mask(_png(path), render_model.OVERLAP_RGB)
    assert red.any()
    # the 0.2 mm runs across x in both cuts (looking along z, and along x it fills the face): at least 10 px wide
    panel1 = red[:, :assembly_sheets.IFACE_PANEL]
    cols = np.nonzero(panel1.any(axis=0))[0]
    assert cols.max() - cols.min() + 1 >= 10
    assert assembly_sheets.CROSS_RGB != render_model.OVERLAP_RGB


def test_check_with_overlap_gets_an_overlap_detail(boxlid, tmp_path):
    unit, items, scene = boxlid
    entry = {"a": "lid", "b": "screw-left", "volume_mm3": 1.2, "location": [-13.6, 0.0, 21.5],
             "size": [0.2, 2.5, 3.0], "planes": [], "status": "fail", "message": "x"}
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    path = assembly_sheets.interface_sheet("box / bolt-left", scene, result, tmp_path / "b.png", overlaps=[entry])
    with Image.open(path) as image:
        assert image.size == (4 * render_model.PANEL, assembly_sheets.TITLE_H + render_model.PANEL)


def test_interface_sheet_culls_items_outside_the_zoom_box(boxlid):
    unit, items, scene = boxlid
    result = ac.check_interface(interface(unit, "bolt-left"), items)
    point, normal = result["planes"][0]
    names = assembly_sheets.visible_items(scene, render_model.Section(tuple(point), tuple(normal)),
                                          result["location"], 40.0 / 490, (520, 520))
    assert {"base", "lid", "screw-left", "nut-left", "battery"} <= set(names)
    assert "screw-right" not in names and "nut-right" not in names


def test_engaged_and_overlap_key_lines():
    assert assembly_sheets.key_lines(overlap=False, engaged=False) == []
    (text, rgb), = assembly_sheets.key_lines(overlap=False, engaged=True)
    assert "engagement" in text and rgb == render_model.ENGAGED_RGB
    (text, rgb), = assembly_sheets.key_lines(overlap=True, engaged=False)
    assert "overlap" in text.lower() and rgb == render_model.OVERLAP_RGB


def test_fault_pairs_from_every_measured_overlap():
    results = [
        {"type": "bolted", "items": ["screw", "block"], "instance": None,
         "measured": {"head_overlap_mm3": {"block": 18.85}, "shank_overlap_mm3": {}, "engagement": {"block": 8}}},
        {"type": "bolted", "items": ["b2.screw", "b2.lid"], "instance": "b2",
         "measured": {"shank_overlap_mm3": {"lid": 1.2}, "head_overlap_mm3": {"lid": 0.0}}},
        {"type": "snap", "items": ["lid", "base"], "measured": {"overlap_mm3": 0.4, "bump_overlap_mm3": 0.4}},
        {"type": "seat", "items": ["lid", "base2"], "measured": {"overlap_mm3": 0.0}},
        {"a": "x", "b": "y", "volume_mm3": 1.0},
    ]
    assert assembly_sheets.fault_pairs(results) == {
        frozenset(("screw", "block")), frozenset(("b2.screw", "b2.lid")), frozenset(("lid", "base")),
        frozenset(("x", "y"))}
