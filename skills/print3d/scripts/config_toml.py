"""Minimal, line-preserving editor for [profile_overrides.<name>] tables in config.toml.

tomllib can only read TOML, and print3d avoids extra dependencies, so this rewrites just one table's lines and
leaves every other byte of the file untouched (comments, other tables, line endings).

The replaced block runs from its header to its last key/value line. Blank lines and comment-only lines that follow
that last key, up to the next table header (or end of file), belong to what follows and are kept byte for byte.
Comment lines between the block's own keys are machine-managed and are dropped on rewrite.
"""
from __future__ import annotations

import tomllib
from pathlib import Path


def _format_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_block(name, values, eol):
    lines = [f"[profile_overrides.{name}]{eol}"]
    for key in sorted(values):
        lines.append(f'"{key}" = {_format_value(values[key])}{eol}')
    return lines


def write_profile_overrides(path, profile_name, values):
    """Replace (or create, or remove when `values` is empty) the [profile_overrides.<profile_name>] table."""
    path = Path(path)
    original = None
    if path.exists():
        with open(path, encoding="utf-8", newline="") as handle:
            original = handle.read()
    lines = original.splitlines(keepends=True) if original else []
    eol = "\r\n" if (original and "\r\n" in original) else "\n"

    header = f"[profile_overrides.{profile_name}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is not None:
        end = next((j for j in range(start + 1, len(lines)) if lines[j].lstrip().startswith("[")), len(lines))
        # Keep the blank and comment-only lines that trail the block; they belong to what follows it.
        while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
            end -= 1
        del lines[start:end]
        insert_at = start
    else:
        insert_at = len(lines)
        if values:  # only touch the file's end when there is a block to append
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += eol
            if lines and lines[-1].strip():
                lines.append(eol)
            insert_at = len(lines)

    if values:
        block = _render_block(profile_name, values, eol)
        if insert_at < len(lines) and lines[insert_at].strip():
            block.append(eol)  # keep a blank line before the following content, without doubling one
        lines[insert_at:insert_at] = block

    text = "".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        if original is None:
            path.unlink()
        else:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(original)
        raise ValueError(f"Writing {path.as_posix()} produced invalid TOML ({exc}); the original was restored") from exc
