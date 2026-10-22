"""Evidence sheets (assembly_sheets): item colours, interface sheets and overview sheets, on the boxlid fixture."""
import types

import numpy as np
import pytest
from PIL import Image

import assembly_checks as ac
import assembly_sheets
import render_model
from test_assembly_fasteners import interface, load


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
