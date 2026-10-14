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
