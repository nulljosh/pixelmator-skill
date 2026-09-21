---
name: pixelmator
description: Build logos, icons and simple graphics live inside Pixelmator Pro on this Mac from a JSON spec (shapes, text, colors, strokes), then export PNG/JPG/SVG/PDF/PXD and verify the result. Use when the user asks to make, draw, design or recreate a logo, icon, badge or wordmark "in Pixelmator", wants to watch it being built in the app, or wants to run AppleScript against Pixelmator Pro with readable errors.
---

# pixelmator

Describe an image. Watch Pixelmator Pro build it, layer by layer. Get a file and an editable document.

Everything goes through one script. It validates the spec, writes the AppleScript, runs it,
decodes any error, then checks the app really built what was asked.

```bash
PXM=~/.claude/skills/pixelmator/pxm.py
python3 $PXM check                       # once per session. Is the app there, is permission granted?
python3 $PXM logo spec.json              # build it on screen, leave the document open
python3 $PXM logo spec.json --headless   # same build in the background, document closed after export
python3 $PXM logo spec.json --gif ~/Downloads/build.gif   # plus a 2 second GIF of it being built, layer by layer
python3 $PXM logo spec.json --dry-run    # print the AppleScript only
python3 $PXM run script.applescript      # anything the spec can't do, with decoded errors
```

## Workflow

1. Write the spec to a file. Canvas first, then layers from bottom to top.
2. Run `logo`. Exit 0 means built, exported and verified.
3. Read the exported PNG with the Read tool. Judge it like a designer. Fix the spec, run again.
4. Exit 2 lists every bad key at once. Fix them all in one pass.

## Spec

```json
{
  "width": 1024, "height": 1024,
  "background": "#FFFFFF",
  "layers": [
    {"type": "rounded_rectangle", "width": 880, "height": 880, "corner_radius": 200, "fill": "#14181F"},
    {"type": "ellipse", "width": 640, "height": 640, "stroke": "#F2B33D", "stroke_width": 28},
    {"type": "text", "text": "JB", "font": "HelveticaNeue-Bold", "size": 300, "color": "#FFFFFF"}
  ],
  "export": ["~/Downloads/logo.png", "~/Downloads/logo.svg"],
  "keep_open": true
}
```

- Leave out `background` for a transparent PNG.
- Layer types: `rectangle`, `rounded_rectangle` (`corner_radius`), `ellipse`, `polygon` (`sides` 3-11),
  `star` (`points` 3-20, `radius` 10-100), `line`, `text` (`text`, `font`, `size`, `color`).
- `cutout` draws curves without a pen tool. `ops` is a list of ovals and rectangles (`shape`: `ellipse` or `rectangle`, whole-pixel `x`, `y`, `width`, `height` in canvas coordinates) combined with `op`: `add`, `subtract` or `intersect`. The result becomes one vector shape. A crescent is an oval minus an oval. A leaf is two circles intersected. See `examples/apple.json`, `nike.json`, `nvidia.json`.
- Every layer takes `x`, `y` (top-left corner in pixels, or `"center"`, the default), `name`, `opacity` 0-100, `rotation` 0-359.9 in degrees, counterclockwise (so 350 tilts a layer 10 degrees clockwise).
- `cx`, `cy` place a layer by its center instead. Use them for text, whose size is only known inside the app. Text on a curve is one text layer per letter, each with `cx`, `cy` and `rotation`.
- Shapes take `width`, `height`, `fill`, `stroke`, `stroke_width`. No fill means outline only.
- Colors are `#RRGGBB`. Export format comes from the extension: png jpg tiff heic webp svg pdf psd pxd.
- Fonts: PostScript names are safest (`HelveticaNeue-Bold`). An unknown font fails the run. Pixelmator would have swapped in Helvetica without a word.

## Text on a curve

```bash
python3 ~/.claude/skills/pixelmator/arc_text.py "Great Service" --font Palatino-Bold --size 88 \
    --cx 1024 --cy 414 --a 1000 --b 344 > letters.json
```

It measures each letter in the real app, spaces them by arc length along the top of the ellipse, tilts each to the tangent, and prints layers to paste into the spec. `--offset` slides the text along the arc.

## Shape recipes

All from `cutout` ops. Real examples sit in `examples/`.

- Crescent or swoosh: oval, subtract a shifted oval, then `rotation` (nike.json).
- Leaf or almond eye: two big circles intersected (apple.json, nvidia.json).
- Ring: oval, subtract a smaller oval. Or stack a white oval on a colored one.
- Arch: tall oval ring, intersect a rectangle to cut it off flat (mcdonalds.json).
- Bite: subtract a circle from the edge (apple.json).
- Two-tone split: build the shape twice, each intersected with a rectangle on one side (nvidia.json).
- Overlap color: intersect the two circles as a third layer (mastercard.json).

## Learned the hard way

- Rotation is counterclockwise. Get it backwards on curved text and every letter leans the wrong way. It reads as "crooked", not as "rotated wrong".
- Judge text from a zoomed crop, never the whole image. `ffmpeg -i logo.png -vf crop=1000:230:300:20 crop.png`, then Read it.
- Compare against the reference side by side before calling a recreation done. Fix the biggest visible difference first.
- A text box is wider than its letters by a fixed padding. Measure, never guess widths.
- Unknown fonts fall back to Helvetica with no error. The verify step catches it. Use PostScript names.
- A new document holds one blank layer. It can only be deleted after another layer exists. The script handles it.
- Shape geometry (corner radius, sides, star points) is read-only after `make`.
- Exporting into a folder that does not exist fails with -100. The script makes folders first.
- GIFs of flat logos: no dithering, one palette for the whole clip. Dithering is what makes text look dirty.
- A headless build takes about six seconds. So iterate: build, Read the PNG, fix the spec, build again.
- Run `uvx ruff check .` and the tests before pushing. CI will fail the push otherwise.

## Exit codes

| Code | Meaning | Do this |
|---|---|---|
| 0 | Built, exported, verified | Look at the image |
| 2 | Bad spec or arguments | Fix the keys it lists |
| 3 | Mac not ready: app missing, Automation permission denied | Follow the hint, ask the user to grant permission |
| 4 | Pixelmator refused a step | Error names the layer and the AppleScript number |
| 5 | Ran, but the result is wrong: layer count, font fallback, export size | Do not ship the file |

## Beyond the spec

Gradients, effects, masks, remove background, image layers: write AppleScript and use `run`.
The dictionary is the source of truth: `sdef "/Applications/Pixelmator Pro.app"`.
Colors there are 0-65535 per channel. Shape geometry is read-only after `make`.
There is no brush, pen or path command. Build from shapes, cutouts and text.
Never script System Events to click through the UI.

## Tests

```bash
cd ~/.claude/skills/pixelmator   # a symlink to ~/Documents/Code/pixelmator-skill
python3 -m unittest -v               # unit tests, no app needed. CI runs these on every push
PXM_LIVE=1 python3 -m unittest -v    # also drives the real app
```
