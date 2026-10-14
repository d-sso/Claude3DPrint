"""print3d command-line interface. Always prints exactly one JSON document to stdout.

    print3d new <part-name> [--dir PARTS_DIR]
    print3d run <model.py> [--set key=value ...] [--formats stl,3mf,step] [--out DIR] [--profile NAME]
                [--no-check] [--check key=value ...] [--strict]
    print3d check <model.py> [--set key=value ...] [--check key=value ...] [--strict] [--profile NAME]
    print3d calibrate [--set key=value ...] [--profile NAME] [--out DIR]
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
COUPON = SKILL_DIR / "calibration" / "calibration_coupon.py"
CALIBRATION_INSTRUCTIONS = [
    "Calibration takes one print. Print calibration_coupon.3mf (coupon flat on the bed, peg on its end, no supports) "
    "with your normal PLA+ settings.",
    "Gauges: each ladder row is tested with one gauge, ideally the smooth shank of a drill bit. Before printing, measure "
    "each gauge shank with calipers. The coupon was built for the diameters in result.parameters[\"gauges\"]; if any "
    "measured shank differs from its value by 0.02 mm or more, rebuild with print3d calibrate --set gauges=<measured "
    "values, comma-separated, e.g. 3.175,3.969,5.159> and print that.",
    "Layout: hold the plate with the notched corner at the far left. The 12 fit sockets are on the left half, the "
    "gauge ladders on the right half (one row per gauge, in the order of gauges, nearest the far edge first), and the "
    "thin fins below the ladders. Identify features by position, not by label (labels may be faint at 0.4 mm nozzle "
    "width, so position is authoritative).",
    "Holes (holes.compensation): each ladder row has 7 plain, uncompensated through-holes, left to right k = 0..6, "
    "sized gauge + 0.1 * k mm. For each row, find the smallest hole k that its gauge enters from the top by hand, "
    "without force, and passes all the way through. That gauge's compensation = 0.1 * k - 0.05 mm. If k = 0 passes, "
    "use 0 and tell the user that slicer XY or hole compensation is probably enabled. If no hole passes, the range is "
    "exceeded: report it and leave that gauge out. holes.compensation = the mean over the gauges, rounded to 0.05 mm. "
    "It is the new absolute value, not an adjustment. If the per-gauge values differ by more than 0.1 mm, report them, "
    "say that compensation varies with hole size, and suggest a follow-up issue for size-dependent compensation.",
    "Fits: the sockets are a grid of 3 rows of 4, each row read left to right. Row 1 (nearest the far edge): "
    "-0.4, -0.3, -0.2, -0.15. Row 2: -0.1, -0.05, 0, 0.05. Row 3 (nearest you): 0.1, 0.2, 0.3, 0.5. Each value is "
    "that socket's label c, a diametral clearance in mm. Try the peg in each socket and, for each fit, take the "
    "smallest c where the peg: press = can be pushed in by hand (firm force, no tools); snug = goes in without force "
    "and stays put when the plate is turned upside down; sliding = rotates and slides freely with no binding; "
    "loose = has clearly visible side-to-side play.",
    "Convert each chosen label with fit = c + used - new, where used = result.profile.used[\"holes.compensation\"] "
    "(the compensation the sockets were built with) and new = holes.compensation from the holes step. This corrects "
    "the sockets for the new compensation, so no second print is needed. Fits may be negative. The values must satisfy "
    "press <= snug <= sliding <= loose; if not, re-test the sockets. If no socket matches a category (for example the "
    "peg is loose even at -0.4), keep the current profile value for that key and tell the user the coupon range was "
    "exceeded.",
    "Peg: measure the printed peg at mid-height with calipers (nominal 10 mm) and report it. It is informational only; "
    "the fits already include it.",
    "Thin fins (labels 0.4, 0.6, 0.8, 1.2, left to right): checks.min_feature = the thinnest fin that printed complete "
    "and full height; checks.min_wall = 2 x that value. Only set these if the result differs from the current profile.",
    "Record: confirm the values with the user, then record them in one command, for example: print3d profile set "
    "holes.compensation=0.2 fits.press=-0.2 fits.snug=-0.15 fits.sliding=-0.05 fits.loose=0.05, and show the result "
    "with print3d profile show.",
]
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
    path = config_path()
    if path.is_file():
        prefix = f"[profile_overrides.{name}."
        if any(line.strip().startswith(prefix) for line in path.read_text(encoding="utf-8").splitlines()):
            return usage_result(
                f"{path.as_posix()}: overrides must be quoted keys under [profile_overrides.{name}], "
                f'e.g. "fits.snug" = 0.15. Sub-tables like [profile_overrides.{name}.fits] cannot be edited by '
                "`print3d profile`; convert them by hand or remove them."
            ), 2
    validated = print3d_profile.load_profile(name, merged)["overrides"]  # raises ProfileError before writing
    if merged == stored:
        return profile_show(config, name), 0
    config_toml.write_profile_overrides(config_path(), name, validated)
    return profile_show(load_config(), name), 0


def config_checks(config):
    checks = config.get("checks", {})
    if not isinstance(checks, dict):
        raise ValueError(f"[checks] in {config_path()} must be a table of threshold = value")
    return checks


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


def cmd_calibrate(args):
    config = load_config()
    profile = resolve_profile(config, args.profile)
    out = args.out or resolve_parts_dir(config) / "calibration" / profile["name"] / "out"
    # The coupon deliberately has fins below min_wall, so printability checks would always report a failure.
    result, code = run_model.run(COUPON, args.overrides, ["stl", "3mf"], out, profile=profile, check=False)
    result["instructions"] = CALIBRATION_INSTRUCTIONS
    return result, code


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
    p_run.add_argument("--no-check", dest="check", action="store_false", help="skip the printability checks")
    _add_check_options(p_run)
    p_check = sub.add_parser("check", help="build a model and run the printability checks, without exporting")
    p_check.add_argument("model", type=Path)
    p_check.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE")
    p_check.add_argument("--profile", help="printer profile name (default: config 'profile')")
    _add_check_options(p_check)
    p_cal = sub.add_parser("calibrate", help="build the calibration coupon for the active profile")
    p_cal.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                       help="coupon parameter, e.g. gauges=3.175,3.969,5.159 (measured gauge diameters)")
    p_cal.add_argument("--profile", help="printer profile name (default: config 'profile')")
    p_cal.add_argument("--out", type=Path, help="output folder (default: <parts_dir>/calibration/<profile>/out)")
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


def _add_check_options(parser):
    parser.add_argument("--check", dest="check_pairs", action="append", default=[], metavar="KEY=VALUE",
                        help="override a check threshold, e.g. min_wall=1.2")
    parser.add_argument("--strict", action="store_true", help="exit with code 3 when any check fails")


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
            if not args.check and (args.strict or args.check_pairs):
                raise UsageError("--no-check cannot be combined with --check or --strict")
            config = load_config()
            profile = resolve_profile(config, args.profile)
            formats = [f.strip().lower() for f in args.formats.split(",") if f.strip()]
            result, code = run_model.run(
                args.model, args.overrides, formats, args.out, profile=profile, check=args.check,
                check_pairs=args.check_pairs, config_checks=config_checks(config), strict=args.strict,
            )
        elif args.command == "check":
            config = load_config()
            result, code = run_model.run(
                args.model, args.overrides, profile=resolve_profile(config, args.profile),
                check_pairs=args.check_pairs, config_checks=config_checks(config), strict=args.strict, export=False,
            )
        elif args.command == "calibrate":
            result, code = cmd_calibrate(args)
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
