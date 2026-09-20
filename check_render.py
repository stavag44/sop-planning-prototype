"""Verifies every chart in index.html actually carries data.

Plotly silently emits empty traces when handed a pandas Series it cannot
serialise, so a page can build, validate and look structurally correct while
rendering nothing. This parses the plotly payloads out of the built page and
fails if any trace has no data.

Run after dashboard.py. Exit code 1 on any empty trace.
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, "index.html")

DATA_KEYS = ("y", "x", "values", "labels", "z")


def traces_from_page(html: str):
    """Pull the data array out of each Plotly.newPlot call."""
    out = []
    for m in re.finditer(r"Plotly\.newPlot\(\s*(\"[^\"]+\"|'[^']+')\s*,\s*(\[)", html):
        start = m.end(2) - 1
        depth, i, instr, esc = 0, start, False, False
        while i < len(html):
            c = html[i]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "[":
                    depth += 1
                elif c == "]":
                    depth -= 1
                    if depth == 0:
                        break
            i += 1
        try:
            out.append(json.loads(html[start:i + 1]))
        except json.JSONDecodeError as e:
            out.append([{"_parse_error": str(e)}])
    return out


def main() -> int:
    if not os.path.exists(PAGE):
        print("FAIL: %s not built" % PAGE)
        return 1
    html = open(PAGE, encoding="utf-8").read()
    charts = traces_from_page(html)
    if not charts:
        print("FAIL: no Plotly.newPlot calls found in the page")
        return 1

    bad, total = [], 0
    for ci, traces in enumerate(charts):
        for ti, tr in enumerate(traces):
            if "_parse_error" in tr:
                bad.append((ci, ti, "(unparseable)", tr["_parse_error"]))
                continue
            total += 1
            name = tr.get("name") or "(unnamed)"
            lens = {}
            for k in DATA_KEYS:
                v = tr.get(k)
                if isinstance(v, list):
                    lens[k] = len(v)
                elif isinstance(v, dict) and "bdata" in v:
                    # plotly 6 encodes numpy arrays as base64 typed arrays
                    lens[k] = len(v["bdata"])
            if not lens or max(lens.values()) == 0:
                bad.append((ci, ti, name, "no data on any of %s" % (DATA_KEYS,)))
                continue
            # an all-null series renders as nothing; usually index-misaligned
            # pandas arithmetic on differently-indexed slices
            y = tr.get("y")
            if isinstance(y, list) and y and all(v is None for v in y):
                bad.append((ci, ti, name, "y is entirely null"))

    print("charts: %d   traces: %d" % (len(charts), total))
    if bad:
        print("\nFAIL: %d empty trace(s)" % len(bad))
        for ci, ti, name, why in bad:
            print("  chart %d trace %d  %-24s %s" % (ci, ti, name, why))
        return 1
    print("all traces carry data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
