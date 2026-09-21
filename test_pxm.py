"""Tests for pxm.py.

    python3 -m unittest -v              unit tests. No Mac, no app needed. This is what CI runs.
    PXM_LIVE=1 python3 -m unittest -v   also drives the real Pixelmator Pro on this Mac.
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import arc_text
import pxm

HERE = os.path.dirname(os.path.abspath(__file__))
APP_PATH = "/Applications/Pixelmator Pro.app"
LIVE = os.environ.get("PXM_LIVE") == "1" and os.path.isdir(APP_PATH)


def spec(**over):
    base = {"width": 100, "height": 100,
            "layers": [{"type": "ellipse", "width": 50, "height": 50, "fill": "#FF0000"}]}
    base.update(over)
    return base


def fake_proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(["osascript"], returncode, stdout, stderr)


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pxm.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Colors(unittest.TestCase):
    def test_hex(self):
        self.assertEqual(pxm.parse_color("#FF0000"), (65535, 0, 0))
        self.assertEqual(pxm.parse_color("#fff"), (65535, 65535, 65535))
        self.assertEqual(pxm.parse_color("#000000"), (0, 0, 0))

    def test_rejects(self):
        for bad in ("red", "FF0000", "#FF00", "#GGGGGG", "", None, 255, "#FF0000; quit"):
            self.assertIsNone(pxm.parse_color(bad), bad)


class Escaping(unittest.TestCase):
    def test_quotes_and_backslashes(self):
        self.assertEqual(pxm.as_string('a"b\\c'), '"a\\"b\\\\c"')
        self.assertEqual(pxm.as_string("two\nlines"), '"two\\nlines"')

    def test_control_characters_refused(self):
        with self.assertRaises(pxm.PxmError):
            pxm.as_string("bell\x07")

    def test_injection_stays_inside_the_string(self):
        evil = 'x"\nend tell\ndo shell script "rm -rf ~"\n--'
        s = pxm.validate_spec(spec(layers=[{"type": "text", "text": evil}]))
        script = pxm.build_script(s)
        self.assertNotIn('\ndo shell script', script)
        # Every quote that came from the text is escaped, so the literal never closes early.
        line = next(ln for ln in script.splitlines() if "text content:" in ln)
        self.assertEqual(len(re.findall(r'(?<!\\)"', line)), 2)


class Validation(unittest.TestCase):
    def assertInvalid(self, bad_spec, *fragments):
        with self.assertRaises(pxm.PxmError) as cm:
            pxm.validate_spec(bad_spec)
        self.assertEqual(cm.exception.code, pxm.EXIT_USAGE)
        for f in fragments:
            self.assertIn(f, cm.exception.message)

    def test_minimal_ok_and_defaults(self):
        s = pxm.validate_spec(spec())
        self.assertTrue(s["keep_open"])
        self.assertEqual(s["layers"][0]["x"], "center")
        self.assertEqual(s["export"], [])

    def test_not_an_object(self):
        self.assertInvalid([], "JSON object")

    def test_reports_every_problem_at_once(self):
        self.assertInvalid(
            {"width": 0, "height": "big", "colour": 1,
             "layers": [{"type": "ellipse", "width": 5, "height": 5, "fill": "red"},
                        {"type": "blob"}]},
            "width:", "height:", "colour: unknown key", "layers[0].fill", "layers[1].type")

    def test_bad_values(self):
        cases = [
            (spec(width=1.5), "width"),
            (spec(width=True), "width"),
            (spec(width=pxm.MAX_SIDE + 1), "width"),
            (spec(width=float("nan")), "width"),
            (spec(layers=[]), "nothing to draw"),
            (spec(layers="nope"), "expected a list"),
            (spec(layers=["nope"]), "expected an object"),
            (spec(keep_open="yes"), "keep_open"),
            (spec(export="logo.exe"), "must end in"),
            (spec(export="logo"), "must end in"),
            (spec(export=5), "export"),
            (spec(layers=[{"type": "ellipse", "width": 5, "height": 5}]), "invisible"),
            (spec(layers=[{"type": "ellipse", "height": 5, "fill": "#000"}]), "width: required"),
            (spec(layers=[{"type": "star", "width": 5, "height": 5, "fill": "#000", "points": 2}]), "points"),
            (spec(layers=[{"type": "polygon", "width": 5, "height": 5, "fill": "#000", "sides": 12}]), "sides"),
            (spec(layers=[{"type": "ellipse", "width": 5, "height": 5, "fill": "#000", "sides": 5}]), "unknown key"),
            (spec(layers=[{"type": "ellipse", "width": 5, "height": 5, "fill": "#000", "x": "left"}]), "center"),
            (spec(layers=[{"type": "ellipse", "width": 5, "height": 5, "fill": "#000", "opacity": 101}]), "opacity"),
            (spec(layers=[{"type": "text", "text": "a", "x": 1, "cx": 1}]), "not both"),
            (spec(layers=[{"type": "text", "text": "a", "cy": "center"}]), "cy"),
            (spec(layers=[{"type": "cutout", "fill": "#000"}]), "ops: expected"),
            (spec(layers=[{"type": "cutout", "fill": "#000", "ops": [
                {"op": "subtract", "x": 0, "y": 0, "width": 5, "height": 5}]}]), "first op must be add"),
            (spec(layers=[{"type": "cutout", "fill": "#000", "ops": [
                {"op": "xor", "shape": "blob", "x": 0.5, "y": 0, "width": 5}]}]), "ops[0].height: required"),
            (spec(layers=[{"type": "text", "text": "  "}]), "text: required"),
            (spec(layers=[{"type": "text", "text": "a", "size": 0}]), "size"),
            (spec(layers=[{"type": "text", "text": "a", "font": ""}]), "font"),
            (spec(layers=[{"type": "text", "text": "a", "fill": "#000"}]), "unknown key"),
        ]
        for bad, fragment in cases:
            with self.subTest(fragment=fragment, spec=bad):
                self.assertInvalid(bad, fragment)

    def test_background_alone_is_enough(self):
        s = pxm.validate_spec({"width": 10, "height": 10, "background": "#000"})
        self.assertEqual(s["background"], (0, 0, 0))

    def test_stroke_gets_a_default_width(self):
        s = pxm.validate_spec(spec(layers=[{"type": "line", "width": 9, "height": 2, "stroke": "#000"}]))
        self.assertEqual(s["layers"][0]["stroke_width"], 4)

    def test_export_paths_made_absolute(self):
        s = pxm.validate_spec(spec(export=["~/x.PNG", "rel/y.svg"]))
        self.assertTrue(all(os.path.isabs(p) for p in s["export"]))
        self.assertNotIn("~", s["export"][0])


class ScriptBuilding(unittest.TestCase):
    def build(self, **over):
        return pxm.build_script(pxm.validate_spec(spec(**over)))

    def test_shape_lines(self):
        script = self.build(layers=[{"type": "star", "width": 40, "height": 40, "points": 5,
                                     "radius": 50, "fill": "#0000FF", "x": 10, "y": 20.5,
                                     "rotation": 45, "opacity": 80, "name": "Spark"}])
        for want in ("make new star shape layer", "star points:5", "star radius:50",
                     "fill color of styles of L to {0, 0, 65535}", "position:{10, 20.5}, width:40",
                     "set rotation of L to 45", "set opacity of L to 80", 'set name of L to "Spark"',
                     'error "layer 0 (star): " & m number n'):
            self.assertIn(want, script)

    def test_stroke_only_shape_has_no_fill(self):
        script = self.build(layers=[{"type": "ellipse", "width": 9, "height": 9, "stroke": "#000"}])
        self.assertIn("set fill opacity of styles of L to 0", script)
        self.assertIn("set stroke width of styles of L to 4", script)

    def test_text_is_centered_in_the_app(self):
        script = self.build(layers=[{"type": "text", "text": "Hi", "font": "Helvetica"}])
        self.assertIn("(100 - (width of L)) / 2", script)
        self.assertIn('"font" & tab & "Helvetica"', script)

    def test_center_anchor(self):
        script = self.build(layers=[{"type": "text", "text": "a", "cx": 30, "cy": 40.5}])
        self.assertIn("set position of L to {30 - (width of L) / 2, 40.5 - (height of L) / 2}", script)

    def test_cutout_combines_selections_into_one_shape(self):
        ops = [{"x": 10, "y": 10, "width": 50, "height": 50},
               {"op": "subtract", "shape": "rectangle", "x": 30, "y": 0, "width": 40, "height": 40}]
        script = self.build(layers=[{"type": "cutout", "fill": "#000", "ops": ops}])
        self.assertIn("draw elliptical selection bounds {10, 10, 50, 50}\n", script)
        self.assertIn("draw selection bounds {30, 0, 40, 40} mode subtract selection", script)
        self.assertLess(script.index("convert selection into shape"), script.index("deselect"))
        self.assertNotIn("set position of L", script)      # drawn in place, stays in place

    def test_background_goes_in_first(self):
        script = self.build(background="#FFFFFF")
        self.assertLess(script.index("rectangle shape layer"), script.index("ellipse shape layer"))

    def test_keep_open_controls_close(self):
        close = "close d saving no"
        self.assertEqual(self.build().count(close), 1)              # only the on-error cleanup
        self.assertEqual(self.build(keep_open=False).count(close), 2)

    def test_headless_never_activates_and_always_closes(self):
        s = pxm.validate_spec(spec())
        self.assertIn("\tactivate\n", pxm.build_script(s))
        quiet = pxm.build_script(s, headless=True)
        self.assertNotIn("activate", quiet)
        self.assertEqual(quiet.count("close d saving no"), 2)

    def test_frames_export_after_every_layer(self):
        s = pxm.validate_spec(spec(background="#000"))
        script = pxm.build_script(s, frames_dir="/tmp/f")
        self.assertIn('(POSIX file "/tmp/f/frame-000.png") as PNG', script)
        self.assertIn("frame-001.png", script)
        self.assertNotIn("frame-002.png", script)
        self.assertNotIn("frame-", pxm.build_script(s))

    def test_blank_layer_is_dropped_once_right_after_the_first_layer(self):
        script = self.build(background="#000")
        self.assertEqual(script.count("delete last layer"), 1)
        self.assertLess(script.index("delete last layer"), script.index("ellipse shape layer"))

    def test_export_format_from_extension(self):
        script = self.build(export=["/tmp/a.png", "/tmp/a.pxd", "/tmp/a.JPG"])
        for want in ('(POSIX file "/tmp/a.png") as PNG', "as Pixelmator Pro", "as JPEG"):
            self.assertIn(want, script)

    def test_tell_blocks_balance(self):
        script = self.build(background="#000", export="/tmp/a.png",
                            layers=[{"type": "text", "text": "a"},
                                    {"type": "line", "width": 5, "height": 5, "stroke": "#fff"}])
        lines = [ln.strip() for ln in script.splitlines()]
        opens = sum(1 for ln in lines if re.match(r"(tell .*(?<! to)|try|with timeout.*)$", ln)
                    and " to get " not in ln)
        ends = sum(1 for ln in lines if ln.startswith("end "))
        self.assertEqual(opens, ends)

    def test_shipped_examples_are_valid(self):
        folder = os.path.join(HERE, "examples")
        names = [n for n in os.listdir(folder) if n.endswith(".json")]
        self.assertTrue(names)
        for name in names:
            with self.subTest(example=name), open(os.path.join(folder, name)) as f:
                self.assertIn("make new document", pxm.build_script(pxm.validate_spec(json.load(f))))


class Paint(unittest.TestCase):
    def test_quadtree_spends_its_layers_where_the_detail_is(self):
        # Left half flat black, right half a checkerboard.
        rows = [[(0, 0, 0)] * 8 + [((x + y) % 2 * 255,) * 3 for x in range(8)] for y in range(16)]
        layers = pxm.paint_layers(16, 16, rows, 41, 10)
        self.assertEqual(len(layers), 41)
        self.assertEqual(layers[0], {"type": "rectangle", "x": 0, "y": 0, "width": 160,
                                     "height": 160, "fill": "#404040"})
        small = [L for L in layers if L["width"] <= 20]
        self.assertTrue(small and all(L["x"] >= 80 for L in small))
        pxm.validate_spec({"width": 160, "height": 160, "layers": layers})

    def test_frames_are_thinned_but_the_last_layer_always_gets_one(self):
        layers = [{"type": "rectangle", "width": 5, "height": 5, "fill": "#000"}] * 7
        script = pxm.build_script(pxm.validate_spec({"width": 9, "height": 9, "layers": layers}),
                                  frames_dir="/f", frame_every=3)
        self.assertEqual(re.findall(r"frame-(\d+)", script), ["000", "001", "002"])


class ArcText(unittest.TestCase):
    def test_letters_mirror_around_the_top_and_tilt_with_the_curve(self):
        layers = arc_text.layout("AA AA", {"A": 50, " ": 25}, 100, 500, 400, 450, 300)
        self.assertEqual(len(layers), 4)                       # the space makes no layer
        first, last = layers[0], layers[-1]
        self.assertAlmostEqual(first["cx"] + last["cx"], 1000, delta=0.2)
        self.assertAlmostEqual(first["cy"], last["cy"], delta=0.2)
        self.assertLess(first["rotation"], 90)                 # left side: counterclockwise tilt
        self.assertGreater(last["rotation"], 270)              # right side: clockwise tilt
        self.assertAlmostEqual(first["rotation"] + last["rotation"], 360, delta=0.2)
        pxm.validate_spec({"width": 1000, "height": 800, "layers": layers})


class ErrorParsing(unittest.TestCase):
    """Every stderr sample here was captured from the real osascript."""

    def test_execution_error(self):
        n, msg = pxm.parse_osascript_error(
            "41:46: execution error: Pixelmator Pro got an error: Can’t get document 99. "
            "Invalid index. (-1719)\n")
        self.assertEqual(n, -1719)
        self.assertIn("document 99", msg)
        self.assertIn("chars 41-46", msg)

    def test_syntax_error(self):
        n, _ = pxm.parse_osascript_error(
            "37:44: syntax error: A identifier can’t go after this identifier. (-2740)")
        self.assertEqual(n, -2740)

    def test_noise_lines_are_skipped(self):
        n, _ = pxm.parse_osascript_error(
            "sandbox_extension_issue_file failed for /tmp/x.png: 2 (No such file or directory)\n"
            "12:20: execution error: Pixelmator Pro got an error: couldn’t be moved. (-100)\n")
        self.assertEqual(n, -100)

    def test_garbage(self):
        self.assertEqual(pxm.parse_osascript_error("kaboom"), (None, "kaboom"))
        self.assertEqual(pxm.parse_osascript_error("")[0], None)

    def test_every_hint_is_a_real_sentence(self):
        for number, hint in pxm.HINTS.items():
            self.assertLess(number, 0)
            self.assertGreater(len(hint), 15)


class Running(unittest.TestCase):
    def test_success_returns_stdout(self):
        with mock.patch("subprocess.run", return_value=fake_proc(stdout="3.8\n")):
            self.assertEqual(pxm.run_applescript("x"), "3.8")

    def test_no_osascript_means_not_a_mac(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaises(pxm.PxmError) as cm:
                pxm.run_applescript("x")
        self.assertEqual(cm.exception.code, pxm.EXIT_ENV)

    def test_hang_becomes_a_clear_error(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("osascript", 5)):
            with self.assertRaises(pxm.PxmError) as cm:
                pxm.run_applescript("x", timeout=5)
        self.assertIn("dialog", cm.exception.hint)

    def test_permission_denied_is_an_environment_problem(self):
        err = "0:10: execution error: Not authorized to send Apple events to Pixelmator Pro. (-1743)"
        with mock.patch("subprocess.run", return_value=fake_proc(1, stderr=err)):
            with self.assertRaises(pxm.PxmError) as cm:
                pxm.run_applescript("x")
        self.assertEqual(cm.exception.code, pxm.EXIT_ENV)
        self.assertIn("Automation", cm.exception.hint)

    def test_dropped_connection_retries_once_then_succeeds(self):
        err = "execution error: Connection is invalid. (-609)"
        with mock.patch("subprocess.run",
                        side_effect=[fake_proc(1, stderr=err), fake_proc(stdout="ok")]) as run:
            self.assertEqual(pxm.run_applescript("x"), "ok")
        self.assertEqual(run.call_count, 2)

    def test_dropped_connection_gives_up_after_one_retry(self):
        err = "execution error: Connection is invalid. (-609)"
        with mock.patch("subprocess.run", return_value=fake_proc(1, stderr=err)) as run:
            with self.assertRaises(pxm.PxmError):
                pxm.run_applescript("x")
        self.assertEqual(run.call_count, 2)

    def test_other_errors_do_not_retry(self):
        err = "1:2: execution error: nope (-1728)"
        with mock.patch("subprocess.run", return_value=fake_proc(1, stderr=err)) as run:
            with self.assertRaises(pxm.PxmError) as cm:
                pxm.run_applescript("x")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(cm.exception.code, pxm.EXIT_SCRIPT)


class Verifying(unittest.TestCase):
    def setUp(self):
        self.spec = pxm.validate_spec(spec(background="#000"))

    def test_layer_count_matches(self):
        pxm.check_result(self.spec, "layers\t2\n")

    def test_layer_count_mismatch(self):
        for output in ("layers\t3\n", "", "layers\tmany\n"):
            with self.subTest(output=output), self.assertRaises(pxm.PxmError) as cm:
                pxm.check_result(self.spec, output)
            self.assertEqual(cm.exception.code, pxm.EXIT_VERIFY)

    def test_silent_font_fallback_is_caught(self):
        with self.assertRaises(pxm.PxmError) as cm:
            pxm.check_result(self.spec, "font\tNoSuchFont\tHelvetica\tHelvetica\nlayers\t2\n")
        self.assertIn("NoSuchFont", cm.exception.message)

    def test_font_names_compare_loosely(self):
        pxm.check_result(self.spec, "font\tHelvetica Neue Bold\tHelvetica\tHelveticaNeue-Bold\nlayers\t2\n")
        pxm.check_result(self.spec, "font\thelvetica\tHelvetica\tHelvetica\nlayers\t2\n")

    def test_missing_and_empty_exports(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "empty.svg")
            open(empty, "w").close()
            for path in (os.path.join(tmp, "gone.svg"), empty):
                s = dict(self.spec, export=[path])
                with self.subTest(path=path), self.assertRaises(pxm.PxmError) as cm:
                    pxm.check_exports(s)
                self.assertEqual(cm.exception.code, pxm.EXIT_VERIFY)

    def test_wrong_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.png")
            with open(path, "w") as f:
                f.write("x")
            s = dict(self.spec, export=[path])
            sips = fake_proc(stdout="  pixelWidth: 64\n  pixelHeight: 64\n")
            with mock.patch("subprocess.run", return_value=sips), self.assertRaises(pxm.PxmError) as cm:
                pxm.check_exports(s)
            self.assertIn("64x64", cm.exception.message)
            good = fake_proc(stdout="  pixelWidth: 100\n  pixelHeight: 100\n")
            with mock.patch("subprocess.run", return_value=good):
                pxm.check_exports(s)


class CommandLine(unittest.TestCase):
    def write(self, tmp, content):
        path = os.path.join(tmp, "spec.json")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_dry_run_prints_script_and_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("subprocess.run") as run:
            code, out, _ = run_main("logo", self.write(tmp, json.dumps(spec())), "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn('tell application "Pixelmator Pro"', out)
        run.assert_not_called()

    def test_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_main("logo", os.path.join(tmp, "missing.json"))[0], pxm.EXIT_USAGE)
            self.assertEqual(run_main("logo", self.write(tmp, "{not json"))[0], pxm.EXIT_USAGE)
            code, _, err = run_main("logo", self.write(tmp, json.dumps(spec(width=-1))))
            self.assertEqual(code, pxm.EXIT_USAGE)
            self.assertIn("hint:", err)

    def test_full_run_with_a_fake_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, json.dumps(spec(export=os.path.join(tmp, "deep/dir/a.svg"))))

            def fake(cmd, **kw):
                with open(os.path.join(tmp, "deep/dir/a.svg"), "w") as f:  # folder must exist by now
                    f.write("<svg/>")
                return fake_proc(stdout="layers\t1\n")

            with mock.patch("subprocess.run", side_effect=fake):
                code, out, err = run_main("logo", path)
        self.assertEqual((code, err), (0, ""))
        self.assertIn("exported:", out)

    def test_app_error_reaches_the_user_with_number_and_hint(self):
        err = "5:9: execution error: Pixelmator Pro got an error: layer 0 (ellipse): no (-10006)"
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("subprocess.run", return_value=fake_proc(1, stderr=err)):
            code, _, stderr = run_main("logo", self.write(tmp, json.dumps(spec())))
        self.assertEqual(code, pxm.EXIT_SCRIPT)
        self.assertIn("AppleScript -10006", stderr)
        self.assertIn("layer 0 (ellipse)", stderr)
        self.assertIn("read-only", stderr)

    def test_gif_needs_ffmpeg_and_a_gif_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, json.dumps(spec()))
            self.assertEqual(run_main("logo", path, "--gif", "out.mov")[0], pxm.EXIT_USAGE)
            with mock.patch("subprocess.run", return_value=fake_proc(stdout="layers\t1\n")), \
                    mock.patch("shutil.which", return_value=None):
                code, _, err = run_main("logo", path, "--gif", os.path.join(tmp, "out.gif"))
        self.assertEqual(code, pxm.EXIT_ENV)
        self.assertIn("brew install ffmpeg", err)

    def test_check_off_the_mac(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(run_main("check")[0], pxm.EXIT_ENV)


class SkillFile(unittest.TestCase):
    def test_frontmatter(self):
        with open(os.path.join(HERE, "SKILL.md")) as f:
            text = f.read()
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        self.assertTrue(m, "SKILL.md needs YAML frontmatter")
        self.assertRegex(m.group(1), r"(?m)^name: pixelmator$")
        self.assertRegex(m.group(1), r"(?m)^description: .{40,}")
        self.assertNotIn(chr(0x2014), text)  # house rule: no em dashes


@unittest.skipUnless(sys.platform == "darwin" and not os.path.isdir(APP_PATH),
                     "needs a Mac without Pixelmator Pro (the CI runner)")
class MacWithoutTheApp(unittest.TestCase):
    def test_check_says_not_installed(self):
        code, _, err = run_main("check")
        self.assertEqual(code, pxm.EXIT_ENV)
        self.assertIn("not installed", err)


@unittest.skipUnless(LIVE, "set PXM_LIVE=1 on a Mac with Pixelmator Pro")
class Live(unittest.TestCase):
    """Drives the real app. Opens and closes documents on screen."""

    def logo(self, tmp, **over):
        with open(os.path.join(HERE, "examples", "badge.json")) as f:
            data = json.load(f)
        data.update(keep_open=False, **over)
        path = os.path.join(tmp, "spec.json")
        with open(path, "w") as f:
            json.dump(data, f)
        return run_main("logo", path)

    def test_check(self):
        self.assertEqual(run_main("check")[0], 0)

    def test_example_builds_exports_and_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            png, svg = os.path.join(tmp, "new dir/a.png"), os.path.join(tmp, "a.svg")
            code, _, err = self.logo(tmp, export=[png, svg])
            self.assertEqual((code, err), (0, ""))
            alpha = subprocess.run(["sips", "-g", "hasAlpha", png], text=True, capture_output=True)
            self.assertIn("hasAlpha: yes", alpha.stdout)
            self.assertIn("<svg", open(svg).read())

    def test_gif_of_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            gif = os.path.join(tmp, "build.gif")
            path = os.path.join(tmp, "s.json")
            with open(os.path.join(HERE, "examples", "badge.json")) as f:
                data = dict(json.load(f), export=[], keep_open=False)
            with open(path, "w") as f:
                json.dump(data, f)
            code, _, err = run_main("logo", path, "--headless", "--gif", gif)
            self.assertEqual((code, err), (0, ""))
            with open(gif, "rb") as f:
                self.assertEqual(f.read(6), b"GIF89a")

    def test_unknown_font_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.logo(tmp, export=[], layers=[
                {"type": "text", "text": "x", "font": "NoSuchFont-XYZ"}])
        self.assertEqual(code, pxm.EXIT_VERIFY)
        self.assertIn("NoSuchFont-XYZ", err)

    def test_raw_script_errors_are_decoded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.applescript")
            with open(path, "w") as f:
                f.write('tell application "Pixelmator Pro" to get width of document 99')
            code, _, err = run_main("run", path)
        self.assertEqual(code, pxm.EXIT_SCRIPT)
        self.assertIn("-1719", err)


if __name__ == "__main__":
    unittest.main()
