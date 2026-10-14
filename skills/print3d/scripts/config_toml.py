"""Minimal, line-preserving editor for [profile_overrides.<name>] tables in config.toml.

tomllib can only read TOML, and print3d avoids extra dependencies, so this rewrites just one table's lines and
leaves every other byte of the file untouched (comments, other tables, line endings).

The replaced block runs from its header to its last key/value line. Blank lines and comment-only lines that follow
that last key, up to the next table header (or end of file), belong to what follows and are kept byte for byte.
Comment lines between the block's own keys are machine-managed and are dropped on rewrite.
"""
from __future__ import annotations

import os
import tempfile
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


def _flatten(table, prefix=""):
    flat = {}
    for key, value in table.items():
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{prefix}{key}."))
        else:
            flat[f"{prefix}{key}"] = value
    return flat


def _same_values(a, b):
    if set(a) != set(b):
        return False
    for key, value in a.items():
        other = b[key]
        try:
            if float(value) != float(other):
                return False
        except (TypeError, ValueError):
            if value != other:
                return False
    return True


def _check_semantics(path, profile_name, original, text, values):
    """Refuse an edit whose parsed result differs from what was intended (e.g. an unrecognised table form)."""
    fail = ValueError(f"Edit of {path.as_posix()} could not safely edit the profile overrides; config not changed")
    parsed = tomllib.loads(text)
    table = parsed.get("profile_overrides", {})
    stored = table.get(profile_name, {}) if isinstance(table, dict) else {}
    if not isinstance(stored, dict) or not _same_values(_flatten(stored), dict(values)):
        raise fail
    try:
        before = tomllib.loads(original) if original is not None else None
    except tomllib.TOMLDecodeError:
        before = None
    if before is None:
        return

    def others(doc):
        rest = {k: v for k, v in doc.items() if k != "profile_overrides"}
        po = doc.get("profile_overrides", {})
        if isinstance(po, dict):
            rest["profile_overrides"] = {k: v for k, v in po.items() if k != profile_name}
        return rest

    if others(before) != others(parsed):
        raise fail


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
    try:
        tomllib.loads(text)  # validate before touching the file, so an invalid result never reaches disk
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"Writing {path.as_posix()} would produce invalid TOML ({exc}); config not changed"
        ) from exc

    _check_semantics(path, profile_name, original, text, values)

    # Write to a temp file in the same folder, then swap it in atomically. An interruption leaves either
    # the old file or the new one, never a truncated file.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
