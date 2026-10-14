"""print3d command-line interface. Always prints exactly one JSON document to stdout.

    print3d new <part-name> [--dir PARTS_DIR]
    print3d run <model.py> [--set key=value ...] [--formats stl,3mf,step] [--out DIR] [--profile NAME]
    print3d profile show|set KEY=VALUE ...|reset [KEY ...] [--profile NAME]
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
import config_toml  # noqa: E402
import print3d_profile  # noqa: E402


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


def _profile_name(config, name):
    return name or config.get("profile") or print3d_profile.DEFAULT_PROFILE


def _flatten(table, prefix=""):
    """Flatten nested tables (from unquoted dotted keys) into dotted keys."""
    flat = {}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _stored_overrides(config, name):
    table = config.get("profile_overrides", {})
    if not isinstance(table, dict):
        raise print3d_profile.ProfileError(f"{config_path().as_posix()}: [profile_overrides] must be a table")
    overrides = table.get(name, {})
    if not isinstance(overrides, dict):
        raise print3d_profile.ProfileError(
            f"{config_path().as_posix()}: [profile_overrides.{name}] must be a table of \"section.key\" = value"
        )
    return _flatten(overrides)


def resolve_profile(config, name=None):
    """The active profile: --profile, else config `profile`, else the default; with that profile's overrides."""
    name = _profile_name(config, name)
    overrides = _stored_overrides(config, name)
    try:
        return print3d_profile.load_profile(name, overrides)
    except print3d_profile.ProfileError as exc:
        if overrides and name in print3d_profile.available_profiles():
            raise print3d_profile.ProfileError(f"{exc} (in {config_path().as_posix()})") from exc
        raise


def profile_show(config, name=None):
    prof = resolve_profile(config, name)
    values = {}
    for section, table in prof["values"].items():
        for key, value in table.items():
            dotted = f"{section}.{key}"
            values[dotted] = {"value": value, "source": "override" if dotted in prof["overrides"] else "base"}
    return {
        "ok": True,
        "profile": prof["name"],
        "title": prof["title"],
        "file": prof["file"],
        "config": config_path().resolve().as_posix(),
        "values": values,
    }


def cmd_profile(args):
    config = load_config()
    name = _profile_name(config, args.profile)
    if args.profile_command == "show":
        return profile_show(config, name), 0
    stored = dict(_stored_overrides(config, name))
    if args.profile_command == "set":
        try:
            new = run_model.parse_overrides(args.pairs)
        except run_model.RunError as exc:
            return usage_result(exc.message.replace("--set", "profile set")), 2
        merged = {**stored, **new}
    else:  # reset
        missing = [key for key in args.keys if key not in stored]
        if missing:
            return usage_result(f"No override to reset for: {', '.join(missing)} (profile {name!r})"), 2
        merged = {k: v for k, v in stored.items() if args.keys and k not in args.keys}
    validated = print3d_profile.load_profile(name, merged)["overrides"]  # raises ProfileError before writing
    config_toml.write_profile_overrides(config_path(), name, validated)
    return profile_show(load_config(), name), 0


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
    p_run.add_argument("--profile", help="printer profile name (default: config 'profile')")
    p_prof = sub.add_parser("profile", help="show or edit the active printer profile's calibration overrides")
    prof_sub = p_prof.add_subparsers(dest="profile_command", required=True)
    p_show = prof_sub.add_parser("show", help="print the merged profile and where each value comes from")
    p_set = prof_sub.add_parser("set", help="record calibrated values, e.g. holes.compensation=0.15")
    p_set.add_argument("pairs", nargs="+", metavar="KEY=VALUE")
    p_reset = prof_sub.add_parser("reset", help="remove overrides (all of them if no keys are given)")
    p_reset.add_argument("keys", nargs="*", metavar="KEY")
    for p in (p_show, p_set, p_reset):
        p.add_argument("--profile", help="printer profile name (default: config 'profile')")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # A name like "-lead" would be parsed as a flag; end option parsing so cmd_new can reject it clearly.
    if (
        len(argv) > 1
        and argv[0] == "new"
        and argv[1].startswith("-")
        and argv[1] not in ("-h", "--help", "--dir")
        and not argv[1].startswith("--dir=")
    ):
        argv.insert(1, "--")
    try:
        args = build_parser().parse_args(argv)
        if args.command == "new":
            parts_dir = args.dir or resolve_parts_dir(load_config())
            result, code = cmd_new(args.name, parts_dir)
        elif args.command == "run":
            profile = resolve_profile(load_config(), args.profile)
            formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
            result, code = run_model.run(args.model, args.overrides, formats, args.out, profile=profile)
        else:
            result, code = cmd_profile(args)
    except UsageError as exc:
        result, code = usage_result(str(exc)), 2
    except print3d_profile.ProfileError as exc:
        result, code = usage_result(str(exc)), 2
    except Exception as exc:  # e.g. an unreadable config file
        result, code = usage_result(f"{type(exc).__name__}: {exc}"), 2
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
