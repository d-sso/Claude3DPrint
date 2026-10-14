import re

import pytest
from conftest import REPO

CHEATSHEET = REPO / "skills" / "print3d" / "references" / "build123d-cheatsheet.md"
BLOCKS = re.findall(r"```python\n(.*?)```", CHEATSHEET.read_text(encoding="utf-8"), re.S) if CHEATSHEET.exists() else []


def test_cheatsheet_has_snippets():
    assert len(BLOCKS) >= 8


@pytest.mark.parametrize("index", range(len(BLOCKS)))
def test_snippet_runs(index):
    exec(compile(BLOCKS[index], f"cheatsheet-block-{index}", "exec"), {})


def test_skill_md_frontmatter():
    text = (REPO / "skills" / "print3d" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: print3d\ndescription: ")
    assert len(text.splitlines()) < 150
