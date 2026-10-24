"""Pure helpers of blender_scene: still list, camera directions, colour conversion, argument parsing."""
import math

import pytest

import blender_scene as bs


def test_default_stills_are_nine():
    assert len(bs.DEFAULT_STILLS) == 9
    assert len(set(bs.DEFAULT_STILLS)) == 9
    assert ("tools", "tools", "studio") in bs.DEFAULT_STILLS
    assert ("assembled", "iso", "dim") in bs.DEFAULT_STILLS


def test_still_plan_product():
    plan = bs.still_plan(("front",), (), ("exploded",), has_tools=True, has_explode=True)
    assert plan == [("exploded", "front", "studio")]


def test_still_plan_defaults_for_missing_axes():
    plan = bs.still_plan((), ("hard", "dim"), (), has_tools=True, has_explode=True)
    assert plan == [("assembled", "iso", "hard"), ("assembled", "iso", "dim")]


def test_still_plan_drops_tools_and_exploded():
    plan = bs.still_plan((), (), (), has_tools=False, has_explode=False)
    assert len(plan) == 6
    assert all(s[0] == "assembled" for s in plan)
    assert bs.still_plan((), (), (), has_tools=True, has_explode=True) == list(bs.DEFAULT_STILLS)


def test_still_name():
    assert bs.still_name("assembled", "iso", "studio") == "assembled-iso-studio.png"


def _close(a, b):
    return all(math.isclose(x, y, abs_tol=1e-9) for x, y in zip(a, b))


def test_view_direction_floor_and_wall():
    assert bs.view_direction("front", "floor", None) == ((0, -1, 0), (0, 0, 1))
    assert bs.view_direction("top", "floor", None) == ((0, 0, 1), (0, 1, 0))
    d, up = bs.view_direction("iso", "floor", None)
    n = math.sqrt(2.64)
    assert _close(d, (1 / n, -1 / n, 0.8 / n)) and up == (0, 0, 1)
    assert bs.view_direction("tools", "floor", None)[0] == d
    d, up = bs.view_direction("front", "wall", (0, -1, 0))
    assert _close(d, (0, -1, 0)) and _close(up, (0, 0, 1))
    d, _ = bs.view_direction("right", "wall", (0, -1, 0))
    assert _close(d, (1, 0, 0))
    d, _ = bs.view_direction("top", "wall", (0, -1, 0))
    assert _close(d, (0, 0, 1))


def test_view_direction_wall_facing_z():
    d, up = bs.view_direction("front", "wall", (0, 0, 1))
    assert _close(d, (0, 0, 1)) and _close(up, (0, 1, 0))
    d, _ = bs.view_direction("top", "wall", (0, 0, 1))
    assert _close(d, (0, 1, 0))
    d, _ = bs.view_direction("right", "wall", (0, 0, 1))
    assert abs(sum(x * y for x, y in zip(d, (0, 0, 1)))) < 1e-9
    d, _ = bs.view_direction("iso", "wall", (0, -1, 0))
    assert math.isclose(math.sqrt(sum(x * x for x in d)), 1.0)


def test_view_direction_rejects_unknown_view():
    with pytest.raises(ValueError):
        bs.view_direction("back", "floor", None)


def test_fit_distance():
    expected = 0.1 / math.sin(math.atan(13.5 / 50)) * 1.1
    assert bs.fit_distance(0.1) == pytest.approx(expected)


def test_srgb_to_linear_endpoints():
    assert bs.srgb_to_linear((0, 0, 0)) == (0.0, 0.0, 0.0)
    assert bs.srgb_to_linear((255, 255, 255)) == pytest.approx((1.0, 1.0, 1.0))
    assert bs.srgb_to_linear((128, 0, 0))[0] == pytest.approx(0.2158, abs=1e-3)


def test_parse_args_still_spec():
    ns = bs.parse_args(["scene.json", "--blend", "o/scene.blend",
                        "--still", r"assembled:iso:studio=C:\out dir\a.png",
                        "--still", "exploded:front:dim=b.png",
                        "--quality", "final", "--resolution", "800x600", "--samples", "8"])
    assert ns.scene_json == "scene.json"
    assert ns.blend == "o/scene.blend"
    assert ns.still == [("assembled", "iso", "studio", r"C:\out dir\a.png"),
                        ("exploded", "front", "dim", "b.png")]
    assert ns.quality == "final"
    assert ns.resolution == (800, 600)
    assert ns.samples == 8


def test_parse_args_defaults():
    ns = bs.parse_args(["scene.json"])
    assert ns.blend is None and ns.still == [] and ns.quality == "draft"
    assert ns.resolution is None and ns.samples is None


def test_module_imports_without_bpy():
    import sys
    assert "bpy" not in sys.modules
