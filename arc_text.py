#!/usr/bin/env python3
"""arc_text: lay text along the top of an ellipse, one text layer per letter.

    python3 arc_text.py "Great Service" --font Palatino-Bold --size 88 \\
        --cx 1024 --cy 414 --a 1000 --b 344 > letters.json

Prints a JSON list of layers to paste into a spec. Letter widths are measured in the real app,
because guessed widths are what make curved text look cheap.
"""
import argparse
import json
import math

import pxm


def measure(chars, font):
    """Advance width of each character at size 100, measured inside Pixelmator Pro."""
    probes = sorted(set(chars) - {" "}) + ["M", "MM", "M M"]
    script = ['set out to ""', 'tell application "%s"' % pxm.APP,
              "set d to make new document with properties {width:600, height:300}", "try", "tell d"]
    for p in probes:
        script += ["set L to make new text layer at the beginning of layers with properties "
                   "{text content:%s}" % pxm.as_string(p), "tell text content of L",
                   "set its font to %s" % pxm.as_string(font), "set its size to 100", "end tell",
                   "set out to out & (width of L) & linefeed"]
    script += ["end tell", "on error m number n", "close d saving no", "error m number n", "end try",
               "close d saving no", "end tell", "return out"]
    widths = dict(zip(probes, (float(x) for x in pxm.run_applescript("\n".join(script)).split())))
    pad = 2 * widths["M"] - widths["MM"]          # every text box carries fixed side padding
    adv = {c: widths[c] - pad for c in probes if len(c) == 1}
    adv[" "] = widths["M M"] - widths["MM"]
    return adv


def layout(text, adv, size, cx, cy, a, b, track=0.0, font=None, color="#FFFFFF", offset=0.0):
    """Pure geometry: center each letter by arc length, tilt it to the tangent."""
    steps, table, s, prev = 20000, [(0.0, 0.0)], 0.0, (0.0, -b)
    for k in range(1, steps + 1):
        th = k * (math.pi / 2) / steps
        p = (a * math.sin(th), -b * math.cos(th))
        s += math.dist(p, prev)
        prev = p
        table.append((s, th))

    def theta(arc):
        lo, hi = 0, steps
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if table[mid][0] < abs(arc) else (lo, mid)
        return math.copysign(table[hi][1], arc)

    widths = [adv[c] * size / 100 + track for c in text]
    pos, layers = offset - sum(widths) / 2, []
    for c, w in zip(text, widths):
        th = theta(pos + w / 2)
        pos += w
        if c == " ":
            continue
        tangent = math.degrees(math.atan2(b * math.sin(th), a * math.cos(th)))
        layer = {"type": "text", "name": "arc " + c, "text": c, "size": size, "color": color,
                 "cx": round(cx + a * math.sin(th), 1), "cy": round(cy - b * math.cos(th), 1),
                 "rotation": round(-tangent % 360, 1) % 360}   # Pixelmator turns counterclockwise
        if font:
            layer["font"] = font
        layers.append(layer)
    return layers


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("text")
    ap.add_argument("--font", default="Helvetica")
    ap.add_argument("--size", type=int, default=72)
    ap.add_argument("--color", default="#FFFFFF")
    ap.add_argument("--track", type=float, default=0, help="extra pixels between letters")
    ap.add_argument("--offset", type=float, default=0, help="slide along the arc, pixels, right is positive")
    for name in ("cx", "cy", "a", "b"):
        ap.add_argument("--" + name, type=float, required=True)
    o = ap.parse_args()
    try:
        print(json.dumps(layout(o.text, measure(o.text, o.font), o.size, o.cx, o.cy, o.a, o.b,
                                o.track, o.font, o.color, o.offset), indent=1))
    except pxm.PxmError as e:
        raise SystemExit("error: %s\n%s" % (e.message, e.hint)) from None
