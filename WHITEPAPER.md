# pixelmator-skill Technical Whitepaper

**v1.0.0** | September 2026

An AI can describe an image. This makes the description build itself inside a real design app, as editable layers, and proves the result before handing it over.

## The core mechanic

Pixelmator Pro ships a full AppleScript dictionary. `pxm.py` is a compiler for it.
Input is a JSON spec: a canvas and a list of layers, bottom to top.
Output is one AppleScript program, run through `osascript`, that makes a document and builds each layer in order.

The pipeline has four stages. Each one can fail on its own, with its own exit code.

1. Validate. The spec is the trust boundary. Types, ranges, unknown keys, colors. Every problem is reported in one pass. Text is escaped so it can never become code.
2. Build. A pure function from spec to script. No app needed, so CI can test it on Linux.
3. Run. One `osascript` call with a timeout. Errors are parsed from stderr and mapped to fixes. Dropped connections retry once. Each layer is wrapped so a failure names the layer. On any failure the document is closed.
4. Verify. The script reports back what it built. Layer count must match. Fonts must have changed, because an unknown font fails silently. Exports must exist at the right pixel size.

## Curves without a pen

The dictionary has no path or brush command. It does have selections with add, subtract and intersect, and a command that converts a selection into a vector shape.
So a `cutout` layer is a list of ovals and rectangles with boolean ops. A crescent is an oval minus an oval. A leaf is two circles intersected. The Apple, Nike and NVIDIA examples are built this way.

## Text on a curve

Text size is only known after the app applies the font. So layers can be placed by center (`cx`, `cy`) and the script measures width inside the app.
Curved text is one layer per letter, placed by arc length along an ellipse and rotated to the tangent. Letter widths come from measuring real glyphs in the app.

## Build GIFs

With `--gif`, the script exports a frame after every layer in the same run. ffmpeg flattens them on white with a fixed palette and no dithering, which keeps flat logo colors and text edges clean.

## Testing

Unit tests cover validation, escaping, script generation, error parsing, retries, verification and exit codes. `osascript` is mocked.
Live tests drive the real app behind `PXM_LIVE=1`. CI runs lint and unit tests on Linux and macOS, Python 3.9 and 3.13. The macOS runner has no Pixelmator Pro, which proves the "not installed" path for real.

## Limits

Approximations only where a logo needs true bezier paths. No gradients or effects in the spec yet. Use `pxm.py run` with raw AppleScript for those.

MIT 2026 Joshua Trommel
