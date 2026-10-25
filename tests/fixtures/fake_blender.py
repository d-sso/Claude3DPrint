"""A stand-in for Blender in the render command tests: run as `python fake_blender.py <blender args> -- <args>`.

It parses the arguments after `--` like blender_scene.py, writes a 1x1 PNG at each --still path, touches --blend,
and prints the PRINT3D_RESULT line (then a banner line, as Blender may). FAKE_BLENDER_FAIL=exit writes one PNG and
exits 3; FAKE_BLENDER_FAIL=noresult prints nothing. blender_scene is imported from PYTHONPATH.
"""
import json
import os
import struct
import sys
import zlib
from pathlib import Path

import blender_scene  # the tests put the skill's scripts on PYTHONPATH


def png_1x1():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\x80\x80\x80")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def main(argv):
    args = blender_scene.parse_args(argv[argv.index("--") + 1:])
    fail = os.environ.get("FAKE_BLENDER_FAIL", "")
    if fail == "noresult":
        return 0
    stills = []
    for state, view, lighting, path in args.still:
        Path(path).write_bytes(png_1x1())
        stills.append({"state": state, "view": view, "lighting": lighting, "path": path, "seconds": 0.01})
        if fail == "exit":
            print("fake blender: render crashed", file=sys.stderr)
            return 3
    blend = Path(args.blend)
    blend.write_bytes(b"BLENDER-fake")
    result = {"ok": True, "blender_version": "4.5.0 (fake)", "engine": "BLENDER_EEVEE_NEXT", "device": "fake",
              "stills": stills, "blend": str(blend), "error": None}
    print("PRINT3D_RESULT " + json.dumps(result), flush=True)
    print("Blender quit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
