import re

from conftest import REPO

SKILL = REPO / "skills" / "print3d"
HELPERS = ("fit(", "hole(", "screw_clearance(", "insert_hole(", "nut_pocket(")


def read(path):
    return path.read_text(encoding="utf-8")


def test_design_rules_exists_and_uses_helpers():
    text = read(SKILL / "references" / "design-rules.md")
    for helper in HELPERS:
        assert helper in text, helper
    for heading in ("Walls", "Overhangs", "Holes", "Fits", "orientation", "inserts", "First layer", "Text", "Snap"):
        assert heading.lower() in text.lower(), heading


def test_skill_md_mentions_profile_workflow_and_stays_short():
    text = read(SKILL / "SKILL.md")
    assert "print3d_profile" in text
    assert "references/design-rules.md" in text
    assert "print3d calibrate" in text
    assert "print3d profile set" in text
    assert "--profile" in text
    assert len(text.splitlines()) < 150


def test_template_imports_helpers():
    text = read(SKILL / "templates" / "model_template.py")
    assert re.search(r"^from print3d_profile import .*fit", text, re.M)


def test_example_config_has_profile_and_overrides_example():
    import tomllib

    text = read(SKILL / "config.example.toml")
    data = tomllib.loads(text)
    assert data["profile"] == "centauri-carbon-pla-plus"
    assert "[profile_overrides.centauri-carbon-pla-plus]" in text  # present as a commented example


def test_readme_documents_profiles():
    text = read(REPO / "README.md")
    for needle in ("print3d calibrate", "print3d profile show", "print3d profile set", "--profile"):
        assert needle in text, needle


def test_documented_helper_calls_are_importable_from_template():
    template = read(SKILL / "templates" / "model_template.py")
    line = re.search(r"^from print3d_profile import (.*)$", template, re.M).group(1)
    imported = {name.strip() for name in line.split("#")[0].split(",")}
    for doc in (SKILL / "references" / "design-rules.md", SKILL / "SKILL.md"):
        text = read(doc)
        for name in ("fit", "hole", "screw_clearance", "insert_hole", "nut_pocket", "profile_value"):
            if re.search(rf"(?<![\w.]){name}\(", text):
                assert name in imported, f"{name} used in {doc.name} but not imported by the template"


def test_docs_cover_printability_checks():
    skill = read(SKILL / "SKILL.md")
    for needle in ("print3d check", "--strict", "--no-check", "expected(p)", "checks.status", "CHECKS = {"):
        assert needle in skill, needle
    assert "<!-- #3" not in skill  # the placeholder is replaced by the check loop
    readme = read(REPO / "README.md")
    for needle in ("print3d check", "--strict", "`3`", "[checks]", "def expected(p)"):
        assert needle in readme, needle
    assert "def expected(p):" in read(SKILL / "templates" / "model_template.py")
    assert "# [checks]" in read(SKILL / "config.example.toml")


def test_design_rules_cover_negative_fits_and_metal_shafts():
    text = read(SKILL / "references" / "design-rules.md").lower()
    assert "negative" in text
    assert "metal shafts" in text
    assert "gauge ladders" in text


def test_skill_md_calibration_is_single_print():
    text = read(SKILL / "SKILL.md")
    assert "reprint" not in text.lower() and "two-pass" not in text.lower()
    assert "fit = c + used - new" in text
    assert "--set gauges=" in text


def test_readme_documents_gauges():
    text = read(REPO / "README.md")
    assert "--set gauges=" in text
    assert "negative" in text.lower()


def test_skill_md_reads_used_from_printed_coupon_result():
    assert "calibration_coupon.result.json" in read(SKILL / "SKILL.md")


def test_skill_documents_thickness_ray_limits():
    text = read(SKILL / "SKILL.md")
    assert "sharp inside corners" in text and "curved thin shells" in text


def test_docs_cover_visual_preview():
    skill = read(SKILL / "SKILL.md")
    assert "<!-- #4" not in skill  # the placeholder is replaced by the visual review checklist
    for needle in ("files.preview", "--no-preview", "Visual review", "orange cut face", "scale bar", "Markers",
                   "the visual review"):
        assert needle in skill, needle
    readme = read(REPO / "README.md")
    for needle in (".preview.png", "--no-preview", "section cuts"):
        assert needle in readme, needle
