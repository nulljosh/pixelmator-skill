#!/usr/bin/env python3
"""pxm: drive Pixelmator Pro live, from a JSON logo spec or raw AppleScript.

Stdlib only. Runs on the system python3 (3.9+).

    pxm.py check                    is this Mac ready?
    pxm.py logo spec.json           build the logo inside the app, export, verify
    pxm.py logo spec.json --dry-run print the AppleScript, touch nothing
    pxm.py logo spec.json --headless same build, app stays in the background
    pxm.py run script.applescript   run raw AppleScript with decoded errors
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

APP = "Pixelmator Pro"

EXIT_USAGE = 2   # bad spec or arguments. Fix the input.
EXIT_ENV = 3     # this Mac is not ready: no app, no permission, not macOS.
EXIT_SCRIPT = 4  # Pixelmator refused something mid-script.
EXIT_VERIFY = 5  # script ran, but the result is not what the spec asked for.

# AppleScript error number -> what to do about it.
HINTS = {
    -1743: "Automation permission denied. System Settings > Privacy & Security > "
           "Automation: allow your terminal (or Claude) to control Pixelmator Pro.",
    -1712: "Pixelmator Pro did not answer in time. It is probably showing a dialog. "
           "Look at the app, dismiss it, run again.",
    -600: "Pixelmator Pro is not running and would not launch. Open it by hand once.",
    -609: "The connection to Pixelmator Pro dropped (it quit or crashed). Run again.",
    -128: "Someone pressed Cancel in the app.",
    -100: "The export folder does not exist or is not writable.",
    -1728: "That object does not exist (wrong layer or document reference), "
           "or Pixelmator Pro is not installed.",
    -1719: "Index out of range: no such layer or document.",
    -1708: "That object does not support that command. Check the dictionary: "
           "sdef '/Applications/Pixelmator Pro.app'",
    -10006: "That property is read-only. Shape geometry (corner radius, sides, star "
            "points) can only be set in 'make ... with properties {...}'.",
    -2740: "AppleScript syntax error. Column numbers above point at the bad token.",
    -2741: "AppleScript syntax error. Column numbers above point at the bad token.",
}
RETRY_ONCE = (-600, -609)

SHAPES = {
    "rectangle": "rectangle shape layer",
    "rounded_rectangle": "rounded rectangle shape layer",
    "ellipse": "ellipse shape layer",
    "polygon": "polygon shape layer",
    "star": "star shape layer",
    "line": "line shape layer",
}
FORMATS = {
    "png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "tiff": "TIFF", "heic": "HEIC",
    "webp": "WebP", "svg": "SVG", "pdf": "PDF", "psd": "PSD", "pxd": "Pixelmator Pro",
}
SIPS_CAN_MEASURE = {"png", "jpg", "jpeg", "tiff", "heic"}
MAX_SIDE = 16384

COMMON_KEYS = {"type", "name", "x", "y", "cx", "cy", "opacity", "rotation"}
SHAPE_KEYS = COMMON_KEYS | {"width", "height", "fill", "stroke", "stroke_width"}
EXTRA_KEYS = {
    "rounded_rectangle": {"corner_radius"},
    "polygon": {"sides"},
    "star": {"points", "radius"},
}
CUTOUT_KEYS = COMMON_KEYS | {"ops", "fill", "stroke", "stroke_width"}
OPS = {"add": "add selection", "subtract": "subtract selection", "intersect": "intersect selection"}
OP_SHAPES = {"ellipse": "draw elliptical selection", "rectangle": "draw selection"}
TEXT_KEYS = COMMON_KEYS | {"text", "font", "size", "color"}
TOP_KEYS = {"width", "height", "background", "layers", "export", "keep_open"}


class PxmError(Exception):
    def __init__(self, message, hint="", code=EXIT_SCRIPT, number=None):
        super().__init__(message)
        self.message, self.hint, self.code, self.number = message, hint, code, number


# ---------- input: the trust boundary ----------

def parse_color(value):
    """'#RRGGBB' or '#RGB' -> (r, g, b) in AppleScript's 0..65535. None if invalid."""
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) * 257 for i in (0, 2, 4))


def as_string(text):
    """Quote text as an AppleScript string literal. Spec text can never become code."""
    out = []
    for ch in text:
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ord(ch) == 127:
            raise PxmError("control character %r in text" % ch, code=EXIT_USAGE)
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_spec(spec):
    """Check everything, report every problem at once, return a normalized copy."""
    errs = []

    def num(obj, key, where, lo, hi, default=None, required=False, integer=False):
        v = obj.get(key, default)
        if v is None:
            if required:
                errs.append("%s%s: required" % (where, key))
            return default
        if not _is_num(v) or (integer and int(v) != v) or not lo <= v <= hi:
            kind = "whole number" if integer else "number"
            errs.append("%s%s: expected a %s from %s to %s, got %r" % (where, key, kind, lo, hi, v))
            return default
        return int(v) if integer else v

    def color(obj, key, where):
        if key not in obj:
            return None
        c = parse_color(obj[key])
        if c is None:
            errs.append('%s%s: expected "#RRGGBB", got %r' % (where, key, obj[key]))
        return c

    def coord(obj, key, where, default):
        v = obj.get(key, default)
        if v is None and default is None:
            return None
        if v != "center" and not (_is_num(v) and abs(v) <= MAX_SIDE * 2):
            errs.append('%s%s: expected a number or "center", got %r' % (where, key, v))
        return v

    if not isinstance(spec, dict):
        raise PxmError("spec must be a JSON object", code=EXIT_USAGE)
    for k in sorted(set(spec) - TOP_KEYS):
        errs.append("%s: unknown key (allowed: %s)" % (k, ", ".join(sorted(TOP_KEYS))))

    out = {
        "width": num(spec, "width", "", 1, MAX_SIDE, required=True, integer=True),
        "height": num(spec, "height", "", 1, MAX_SIDE, required=True, integer=True),
        "background": color(spec, "background", ""),
        "keep_open": spec.get("keep_open", True),
        "layers": [],
        "export": [],
    }
    if not isinstance(out["keep_open"], bool):
        errs.append("keep_open: expected true or false, got %r" % (out["keep_open"],))

    layers = spec.get("layers", [])
    if not isinstance(layers, list):
        errs.append("layers: expected a list")
        layers = []
    if not layers and "background" not in spec:
        errs.append("layers: nothing to draw (give layers or a background)")

    for i, raw in enumerate(layers):
        where = "layers[%d]." % i
        if not isinstance(raw, dict):
            errs.append("layers[%d]: expected an object" % i)
            continue
        kind = raw.get("type")
        if kind not in ("text", "cutout") and kind not in SHAPES:
            errs.append("%stype: expected one of %s, got %r"
                        % (where, ", ".join(sorted(list(SHAPES) + ["text", "cutout"])), kind))
            continue
        allowed = {"text": TEXT_KEYS, "cutout": CUTOUT_KEYS}.get(
            kind, SHAPE_KEYS | EXTRA_KEYS.get(kind, set()))
        for k in sorted(set(raw) - allowed):
            errs.append("%s%s: unknown key for a %s layer" % (where, k, kind))

        layer = {
            "type": kind,
            "name": raw.get("name"),
            # A cutout is drawn in canvas coordinates, so it stays put unless told to move.
            "x": coord(raw, "x", where, None if kind == "cutout" else "center"),
            "y": coord(raw, "y", where, None if kind == "cutout" else "center"),
            # cx/cy place the layer by its center. Needed when the size is only known in the app.
            "cx": num(raw, "cx", where, -MAX_SIDE, MAX_SIDE * 2),
            "cy": num(raw, "cy", where, -MAX_SIDE, MAX_SIDE * 2),
            "opacity": num(raw, "opacity", where, 0, 100, integer=True),
            "rotation": num(raw, "rotation", where, 0, 359),
        }
        for a, b in (("x", "cx"), ("y", "cy")):
            if a in raw and b in raw:
                errs.append("%s%s: give %s or %s, not both" % (where, a, a, b))
        if layer["name"] is not None and not isinstance(layer["name"], str):
            errs.append("%sname: expected text" % where)

        if kind == "cutout":
            # Curves without a pen tool: combine ovals and rectangles, then turn the result
            # into one vector shape.
            ops = raw.get("ops")
            if not isinstance(ops, list) or not ops:
                errs.append("%sops: expected a non-empty list" % where)
                ops = []
            clean = []
            for j, op in enumerate(ops):
                w2 = "%sops[%d]." % (where, j)
                if not isinstance(op, dict):
                    errs.append("%sops[%d]: expected an object" % (where, j))
                    continue
                for k in sorted(set(op) - {"op", "shape", "x", "y", "width", "height"}):
                    errs.append("%s%s: unknown key" % (w2, k))
                if op.get("op", "add") not in OPS:
                    errs.append("%sop: expected add, subtract or intersect, got %r" % (w2, op.get("op")))
                if op.get("shape", "ellipse") not in OP_SHAPES:
                    errs.append("%sshape: expected ellipse or rectangle, got %r" % (w2, op.get("shape")))
                clean.append({
                    "op": op.get("op", "add"), "shape": op.get("shape", "ellipse"),
                    "x": num(op, "x", w2, -MAX_SIDE, MAX_SIDE, required=True, integer=True),
                    "y": num(op, "y", w2, -MAX_SIDE, MAX_SIDE, required=True, integer=True),
                    "width": num(op, "width", w2, 1, MAX_SIDE, required=True, integer=True),
                    "height": num(op, "height", w2, 1, MAX_SIDE, required=True, integer=True)})
            if clean and clean[0]["op"] != "add":
                errs.append("%sops[0].op: the first op must be add" % where)
            layer.update(ops=clean, fill=color(raw, "fill", where), stroke=color(raw, "stroke", where),
                         stroke_width=num(raw, "stroke_width", where, 0, 200))
            if "fill" not in raw and "stroke" not in raw:
                errs.append("%sfill: a shape needs a fill or a stroke, or it is invisible" % where)
            if "stroke" in raw and not layer["stroke_width"]:
                layer["stroke_width"] = 4
        elif kind == "text":
            text = raw.get("text")
            if not isinstance(text, str) or not text.strip():
                errs.append("%stext: required, non-empty" % where)
            font = raw.get("font")
            if font is not None and (not isinstance(font, str) or not font.strip()):
                errs.append("%sfont: expected a font name" % where)
            layer.update(text=text, font=font, color=color(raw, "color", where),
                         size=num(raw, "size", where, 1, 5000, default=72, integer=True))
        else:
            layer.update(
                width=num(raw, "width", where, 1, MAX_SIDE, required=True),
                height=num(raw, "height", where, 1, MAX_SIDE, required=True),
                fill=color(raw, "fill", where), stroke=color(raw, "stroke", where),
                stroke_width=num(raw, "stroke_width", where, 0, 200),
                corner_radius=num(raw, "corner_radius", where, 0, MAX_SIDE),
                sides=num(raw, "sides", where, 3, 11, integer=True),
                points=num(raw, "points", where, 3, 20, integer=True),
                radius=num(raw, "radius", where, 10, 100),
            )
            if "fill" not in raw and "stroke" not in raw:
                errs.append("%sfill: a shape needs a fill or a stroke, or it is invisible" % where)
            if "stroke" in raw and not layer["stroke_width"]:
                layer["stroke_width"] = 4
        out["layers"].append(layer)

    exports = spec.get("export", [])
    if isinstance(exports, str):
        exports = [exports]
    if not isinstance(exports, list):
        errs.append("export: expected a path or a list of paths")
        exports = []
    for p in exports:
        ext = p.rsplit(".", 1)[-1].lower() if isinstance(p, str) and "." in p else ""
        if ext not in FORMATS:
            errs.append("export: %r must end in one of: %s" % (p, ", ".join(sorted(FORMATS))))
            continue
        out["export"].append(os.path.abspath(os.path.expanduser(p)))

    if errs:
        raise PxmError("invalid spec:\n  " + "\n  ".join(errs),
                       hint="Fix the listed keys. See SKILL.md for the full spec.", code=EXIT_USAGE)
    return out


# ---------- spec -> AppleScript (pure, testable anywhere) ----------

def _rgb(c):
    return "{%d, %d, %d}" % c


def _n(v):
    return repr(round(float(v), 3)) if not float(v).is_integer() else str(int(v))


def build_script(spec, timeout=120, headless=False, frames_dir=None):
    """Normalized spec -> AppleScript. Layers are listed bottom to top.

    headless: never bring the app forward, always close the document after export.
    frames_dir: also export frame-000.png, frame-001.png... there, one after every layer.
    """
    W, H = spec["width"], spec["height"]
    layers = list(spec["layers"])
    if spec["background"]:
        layers.insert(0, {"type": "rectangle", "name": "Background", "x": 0, "y": 0,
                          "width": W, "height": H, "fill": spec["background"],
                          "stroke": None, "stroke_width": None, "opacity": None,
                          "rotation": None, "cx": None, "cy": None})
    body = []
    for i, L in enumerate(layers):
        label = "layer %d (%s)" % (i, L["type"])
        b = ["try"]
        if L["type"] == "text":
            b.append("set L to make new text layer at the beginning of layers "
                     "with properties {text content:%s}" % as_string(L["text"]))
            b.append("tell text content of L")
            b.append("\tset fontBefore to its font")
            if L["color"]:
                b.append("\tset its color to %s" % _rgb(L["color"]))
            if L["font"]:
                b.append("\tset its font to %s" % as_string(L["font"]))
            b.append("\tset its size to %d" % L["size"])
            b.append("\tset fontAfter to its font")
            b.append("end tell")
            if L["font"]:
                b.append('set out to out & "font" & tab & %s & tab & fontBefore & tab '
                         "& fontAfter & linefeed" % as_string(L["font"]))
        elif L["type"] == "cutout":
            for j, op in enumerate(L["ops"]):
                b.append("%s bounds {%d, %d, %d, %d}%s" % (
                    OP_SHAPES[op["shape"]], op["x"], op["y"], op["width"], op["height"],
                    "" if j == 0 else " mode " + OPS[op["op"]]))
            b += ["convert selection into shape", "set L to current layer", "deselect"]
        else:
            props = ["position:{0, 0}", "width:%s" % _n(L["width"]), "height:%s" % _n(L["height"])]
            for key, term in (("corner_radius", "corner radius"), ("sides", "sides"),
                              ("points", "star points"), ("radius", "star radius")):
                if L.get(key) is not None:
                    props.append("%s:%s" % (term, _n(L[key])))
            b.append("set L to make new %s at the beginning of layers with properties {%s}"
                     % (SHAPES[L["type"]], ", ".join(props)))
        if L["type"] != "text":
            if L["fill"]:
                b.append("set fill color of styles of L to %s" % _rgb(L["fill"]))
            else:
                b.append("set fill opacity of styles of L to 0")
            if L["stroke"]:
                b.append("set stroke color of styles of L to %s" % _rgb(L["stroke"]))
                b.append("set stroke width of styles of L to %s" % _n(L["stroke_width"]))
        if L.get("name"):
            b.append("set name of L to %s" % as_string(L["name"]))
        # Measured in the app: text size is only known after the font is applied.
        here = "item %d of (get position of L)"
        x = "(%d - (width of L)) / 2" % W if L["x"] == "center" else here % 1 if L["x"] is None else _n(L["x"])
        y = "(%d - (height of L)) / 2" % H if L["y"] == "center" else here % 2 if L["y"] is None else _n(L["y"])
        if L.get("cx") is not None:
            x = "%s - (width of L) / 2" % _n(L["cx"])
        if L.get("cy") is not None:
            y = "%s - (height of L) / 2" % _n(L["cy"])
        if L["type"] != "cutout" or any(L.get(k) is not None for k in ("x", "y", "cx", "cy")):
            b.append("set position of L to {%s, %s}" % (x, y))
        if L.get("rotation"):
            b.append("set rotation of L to %s" % _n(L["rotation"]))
        if L.get("opacity") is not None:
            b.append("set opacity of L to %d" % L["opacity"])
        b += ["on error m number n", "\terror %s & m number n" % as_string(label + ": "), "end try"]
        if i == 0:
            # A new document arrives with one blank image layer. Drop it so PNGs keep alpha.
            # It can only go once another layer exists.
            b.append("delete last layer")
        if frames_dir:
            b.append("export d to (POSIX file %s) as PNG"
                     % as_string(os.path.join(frames_dir, "frame-%03d.png" % i)))
        body += ["\t\t" + line for line in b]

    exports = ["\t\texport d to (POSIX file %s) as %s"
               % (as_string(p), FORMATS[p.rsplit(".", 1)[-1].lower()]) for p in spec["export"]]
    keep_open = spec["keep_open"] and not headless
    lines = [
        "with timeout of %d seconds" % timeout,
        'tell application "%s"' % APP,
    ] + ([] if headless else ["\tactivate"]) + [
        "\tset d to make new document with properties {width:%d, height:%d}" % (W, H),
        '\tset out to ""',
        "\ttry",
        "\t\ttell d",
    ] + ["\t" + line for line in body] + [
        '\t\t\tset out to out & "layers" & tab & (count of layers) & linefeed',
        "\t\tend tell",
    ] + exports + [
        "\ton error m number n",
        "\t\tclose d saving no",  # never leave a half-built document behind
        "\t\terror m number n",
        "\tend try",
    ] + ([] if keep_open else ["\tclose d saving no"]) + [
        "\treturn out",
        "end tell",
        "end timeout",
    ]
    return "\n".join(lines) + "\n"


# ---------- running AppleScript ----------

_ERR_LINE = re.compile(r"^(?:(\d+):(\d+): )?(syntax|execution) error: (.*?)\s*\((-?\d+)\)\s*$")


def parse_osascript_error(stderr):
    """Last 'error: message (number)' line of osascript stderr -> (number, message)."""
    for line in reversed(stderr.strip().splitlines()):
        m = _ERR_LINE.match(line.strip())
        if m:
            where = "chars %s-%s: " % (m.group(1), m.group(2)) if m.group(1) else ""
            return int(m.group(5)), where + m.group(4)
    return None, stderr.strip() or "osascript failed with no message"


def run_applescript(source, timeout=120, _retry=True):
    try:
        p = subprocess.run(["osascript", "-"], input=source, text=True,
                           capture_output=True, timeout=timeout + 10)
    except FileNotFoundError:
        raise PxmError("osascript not found", hint="This skill only works on macOS.",
                       code=EXIT_ENV) from None
    except subprocess.TimeoutExpired:
        raise PxmError("no answer from %s after %ds" % (APP, timeout), hint=HINTS[-1712],
                       number=-1712) from None
    if p.returncode == 0:
        return p.stdout.strip()
    number, message = parse_osascript_error(p.stderr)
    if number in RETRY_ONCE and _retry:
        return run_applescript(source, timeout, _retry=False)
    code = EXIT_ENV if number in (-1743, -600) else EXIT_SCRIPT
    raise PxmError(message, hint=HINTS.get(number, ""), code=code, number=number)


# ---------- after the run: trust nothing ----------

def _squash(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def check_result(spec, output):
    """Compare what Pixelmator says it built with what the spec asked for."""
    expected = len(spec["layers"]) + (1 if spec["background"] else 0)
    count = None
    for line in output.splitlines():
        parts = line.split("\t")
        if parts[0] == "layers" and len(parts) == 2 and parts[1].strip().isdigit():
            count = int(parts[1])
        elif parts[0] == "font" and len(parts) == 4:
            asked, before, after = parts[1:]
            # Pixelmator ignores unknown fonts without an error. Catch the silent fallback.
            # ponytail: a display-name alias of the default font reads as a false alarm;
            # use the PostScript name if that ever bites.
            if after == before and _squash(asked) != _squash(after):
                raise PxmError("font %r was not applied (still %r)" % (asked, after),
                               hint="Use the exact name from Font Book, e.g. 'HelveticaNeue-Bold'.",
                               code=EXIT_VERIFY)
    if count != expected:
        raise PxmError("expected %d layers in the document, Pixelmator reports %s"
                       % (expected, count), code=EXIT_VERIFY)


def check_exports(spec):
    for path in spec["export"]:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise PxmError("export missing or empty: %s" % path, code=EXIT_VERIFY)
        if path.rsplit(".", 1)[-1].lower() not in SIPS_CAN_MEASURE:
            continue
        p = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
                           text=True, capture_output=True)
        dims = [int(n) for n in re.findall(r"pixel(?:Width|Height): (\d+)", p.stdout)]
        if dims != [spec["width"], spec["height"]]:
            raise PxmError("%s is %s, spec says %dx%d"
                           % (path, "x".join(map(str, dims)) or "unreadable",
                              spec["width"], spec["height"]), code=EXIT_VERIFY)


def make_gif(frames_dir, out_path, width, height, seconds=2.0):
    """Frames -> looping GIF on white, about `seconds` of build plus a 2 second hold."""
    if not shutil.which("ffmpeg"):
        raise PxmError("ffmpeg not found", hint="brew install ffmpeg", code=EXIT_ENV)
    frames = len([n for n in os.listdir(frames_dir) if n.startswith("frame-")])
    if not frames:
        raise PxmError("no frames were exported", code=EXIT_VERIFY)
    graph = ("color=white:s=%dx%d[bg];[bg][0:v]overlay=shortest=1,"
             "tpad=stop_mode=clone:stop_duration=2,scale=%d:-1:flags=lanczos,"
             # Flat logo colors: a full-clip palette and no dithering keep text edges clean.
             "split[a][b];[a]palettegen=stats_mode=full[p];[b][p]paletteuse=dither=none:diff_mode=none"
             % (width, height, min(width, 1000)))
    p = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate",
                        "%.3f" % max(frames / seconds, 1), "-i",
                        os.path.join(frames_dir, "frame-%03d.png"), "-filter_complex", graph,
                        "-loop", "0", out_path], text=True, capture_output=True)
    if p.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise PxmError("ffmpeg could not write %s: %s" % (out_path, p.stderr.strip()[-300:]),
                       code=EXIT_VERIFY)


# ---------- commands ----------

def cmd_check(_args):
    if sys.platform != "darwin":
        raise PxmError("not macOS", hint="Pixelmator Pro only exists on the Mac.", code=EXIT_ENV)
    try:
        run_applescript('id of application "%s"' % APP, timeout=20)
    except PxmError as e:
        if e.code == EXIT_ENV:
            raise
        raise PxmError("%s is not installed" % APP, hint="Install it from the Mac App Store.",
                       code=EXIT_ENV) from None
    version = run_applescript('tell application "%s" to get version' % APP, timeout=60)
    print("ok: %s %s answers AppleScript" % (APP, version))


def cmd_run(args):
    source = sys.stdin.read() if args.file == "-" else open(args.file).read()
    print(run_applescript(source, timeout=args.timeout))


def cmd_logo(args):
    with open(args.spec) as f:
        spec = validate_spec(json.load(f))
    if args.gif and not args.gif.lower().endswith(".gif"):
        raise PxmError("--gif path must end in .gif", code=EXIT_USAGE)
    frames_dir = tempfile.mkdtemp(prefix="pxm-frames-") if args.gif and not args.dry_run else None
    try:
        script = build_script(spec, timeout=args.timeout, headless=args.headless,
                              frames_dir=frames_dir or ("/tmp/frames" if args.gif else None))
        if args.dry_run:
            print(script, end="")
            return
        gif = os.path.abspath(os.path.expanduser(args.gif)) if args.gif else None
        for path in spec["export"] + ([gif] if gif else []):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        check_result(spec, run_applescript(script, timeout=args.timeout))
        check_exports(spec)
        if gif:
            make_gif(frames_dir, gif, spec["width"], spec["height"])
    finally:
        if frames_dir:
            shutil.rmtree(frames_dir, ignore_errors=True)
    for path in spec["export"] + ([gif] if gif else []):
        print("exported: %s" % path)
    print("ok: %d layers, %s" % (len(spec["layers"]),
                                 "left open in %s" % APP
                                 if spec["keep_open"] and not args.headless else "closed"))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pxm.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="is this Mac ready?").set_defaults(fn=cmd_check)
    r = sub.add_parser("run", help="run raw AppleScript ('-' reads stdin)")
    r.add_argument("file")
    r.add_argument("--timeout", type=int, default=120)
    r.set_defaults(fn=cmd_run)
    g = sub.add_parser("logo", help="build a logo from a JSON spec")
    g.add_argument("spec")
    g.add_argument("--dry-run", action="store_true", help="print the AppleScript and stop")
    g.add_argument("--headless", action="store_true",
                   help="stay in the background: no focus steal, close the document after export")
    g.add_argument("--gif", metavar="PATH", help="also write a short GIF of the build (needs ffmpeg)")
    g.add_argument("--timeout", type=int, default=120)
    g.set_defaults(fn=cmd_logo)
    args = ap.parse_args(argv)
    try:
        args.fn(args)
        return 0
    except PxmError as e:
        err = e
    except json.JSONDecodeError as e:
        err = PxmError("spec is not valid JSON: %s" % e, code=EXIT_USAGE)
    except OSError as e:
        err = PxmError(str(e), code=EXIT_USAGE)
    except KeyboardInterrupt:
        return 130
    tag = " (AppleScript %d)" % err.number if err.number is not None else ""
    print("error%s: %s" % (tag, err.message), file=sys.stderr)
    if err.hint:
        print("hint: %s" % err.hint, file=sys.stderr)
    return err.code


if __name__ == "__main__":
    sys.exit(main())
