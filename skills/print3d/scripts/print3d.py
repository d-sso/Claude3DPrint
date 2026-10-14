"""print3d command-line interface. Always prints exactly one JSON document to stdout.

    print3d new <part-name> [--dir PARTS_DIR]
    print3d run <model.py> [--set key=value ...] [--formats stl,3mf,step] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPTS_DIR.parent
TEMPLATE = SKILL_DIR / "templates" / "model_template.py"
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

sys.path.insert(0, str(SCRIPTS_DIR))
import run_model  # noqa: E402


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def config_path():
    env = os.environ.get("PRINT3D_CONFIG")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "print3d" / "config.toml"


def load_config():
    import tomllib

    path = config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid config file {path}: {exc}") from exc


def resolve_parts_dir(config):
    return Path(config.get("parts_dir", Path.home() / "3DParts")).expanduser()


def usage_result(message):
    return {
        "ok": False,
        "error": {"stage": "usage", "type": "UsageError", "message": message, "line": None, "traceback": []},
    }


def cmd_new(name, parts_dir):
    if not NAME_RE.match(name):
        return usage_result(
            f"Part name {name!r} must be kebab-case (lowercase letters, digits, single hyphens), e.g. 'phone-stand'"
        ), 2
    folder = Path(parts_dir) / name
    if folder.exists():
        return usage_result(f"{folder.as_posix()} already exists; pick another name or edit the existing model"), 2
    folder.mkdir(parents=True)
    model = folder / f"{name}.py"
    shutil.copyfile(TEMPLATE, model)
    return {"ok": True, "model": model.resolve().as_posix()}, 0


def build_parser():
    parser = _Parser(prog="print3d", description="Build parametric build123d models into printable files.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_new = sub.add_parser("new", help="create a new part from the template")
    p_new.add_argument("name", help="kebab-case part name, e.g. phone-stand")
    p_new.add_argument("--dir", type=Path, help="parts folder (default: parts_dir from config)")
    p_run = sub.add_parser("run", help="build a model and export it")
    p_run.add_argument("model", type=Path)
    p_run.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    p_run.add_argument("--formats", default="stl,3mf", help="comma-separated: stl,3mf,step")
    p_run.add_argument("--out", type=Path, help="output folder (default: <model dir>/out)")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # A name like "-lead" would be parsed as a flag; end option parsing so cmd_new can reject it clearly.
    if len(argv) > 1 and argv[0] == "new" and argv[1].startswith("-") and argv[1] not in ("-h", "--help", "--dir")             and not argv[1].startswith("--dir="):
        argv.insert(1, "--")
    try:
        args = build_parser().parse_args(argv)
        if args.command == "new":
            parts_dir = args.dir or resolve_parts_dir(load_config())
            result, code = cmd_new(args.name, parts_dir)
        else:
            formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
            result, code = run_model.run(args.model, args.overrides, formats, args.out)
    except UsageError as exc:
        result, code = usage_result(str(exc)), 2
    except Exception as exc:  # e.g. an unreadable config file
        result, code = usage_result(f"{type(exc).__name__}: {exc}"), 2
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
