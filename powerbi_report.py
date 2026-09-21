"""Builds the report layout for the Power BI project.

Kept separate from powerbi_build.py because the two formats have nothing in
common. The model is clean TMSL. The report is a JSON file in which each
visual's config is itself a JSON *string*, so everything here is built as
dicts and serialised at the last moment; hand-writing the escaped form is how
the file ends up unopenable.

Run after powerbi_build.py.
"""

from __future__ import annotations

import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Nextpower SOP"
REPORT_DIR = os.path.join(HERE, "pbi", NAME + ".Report")

W, H = 1280, 720
ORANGE = "#FE5000"
INK = "#111827"
MUTED = "#737373"
PAPER = "#FFFFFF"
SURFACE = "#F5F5F5"

# must match MEASURE_TABLE in powerbi_build.py. "Measures" is reserved.
MEAS = "S&OP Metrics"


def vid() -> str:
    return uuid.uuid4().hex[:20]


# ------------------------------------------------------------------ query parts
def _src(alias: str) -> dict:
    return {"SourceRef": {"Source": alias}}


def measure_sel(name: str, alias: str = "m") -> dict:
    return {"Measure": {"Expression": _src(alias), "Property": name},
            "Name": "%s.%s" % (MEAS, name)}


def column_sel(table: str, col: str, alias: str) -> dict:
    return {"Column": {"Expression": _src(alias), "Property": col},
            "Name": "%s.%s" % (table, col)}


def proto(entities: list[tuple[str, str]], selects: list[dict],
          where: list | None = None, orderby: list | None = None) -> dict:
    q = {
        "Version": 2,
        "From": [{"Name": a, "Entity": e, "Type": 0} for a, e in entities],
        "Select": selects,
    }
    if where:
        q["Where"] = where
    if orderby:
        q["OrderBy"] = orderby
    return q


# ------------------------------------------------------------------ containers
def container(x, y, w, h, z, single: dict, name: str | None = None) -> dict:
    name = name or vid()
    cfg = {
        "name": name,
        "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": z,
                                           "width": w, "height": h}}],
        "singleVisual": single,
    }
    return {"x": x, "y": y, "z": z, "width": w, "height": h,
            "config": json.dumps(cfg)}


def _obj(props: dict) -> dict:
    """Wrap literal formatting values in the shape the layout expects."""
    out = {}
    for k, v in props.items():
        if isinstance(v, str) and v.startswith("#"):
            out[k] = {"solid": {"color": {"expr": {"Literal": {"Value": "'%s'" % v}}}}}
        elif isinstance(v, bool):
            out[k] = {"expr": {"Literal": {"Value": "true" if v else "false"}}}
        elif isinstance(v, (int, float)):
            out[k] = {"expr": {"Literal": {"Value": "%sD" % v}}}
        else:
            out[k] = {"expr": {"Literal": {"Value": "'%s'" % v}}}
    return out


def card(x, y, w, h, z, measure: str, label: str) -> dict:
    single = {
        "visualType": "card",
        "projections": {"Values": [{"queryRef": "%s.%s" % (MEAS, measure)}]},
        "prototypeQuery": proto([("m", MEAS)], [measure_sel(measure)]),
        "objects": {
            # labelDisplayUnits 1 = None. The measure format strings already
            # scale to millions and append "M"; leaving the visual on Auto
            # scaled a second time and appended a second "M", so $267,492,950
            # rendered as "$0MM".
            "labels": [{"properties": _obj({"color": INK, "fontSize": 28,
                                            "fontFamily": "Segoe UI Semibold",
                                            "labelDisplayUnits": 1,
                                            "labelPrecision": 0})}],
            "categoryLabels": [{"properties": _obj({"color": MUTED,
                                                    "fontSize": 10})}],
        },
        "vcObjects": {
            "title": [{"properties": _obj({"text": label, "show": True,
                                           "fontColor": MUTED, "fontSize": 9})}],
            "background": [{"properties": _obj({"color": PAPER,
                                                "transparency": 0})}],
            "border": [{"properties": _obj({"show": True, "color": "#E5E5E5"})}],
        },
        "drillFilterOtherVisuals": True,
    }
    return container(x, y, w, h, z, single)


def chart(x, y, w, h, z, vtype: str, cat: tuple[str, str], measures: list[str],
          title: str, colors: list[str] | None = None,
          legend: bool = True, cat_role: str = "Category",
          val_role: str = "Y") -> dict:
    table, col = cat
    selects = [column_sel(table, col, "c")] + [measure_sel(m) for m in measures]
    entities = [("c", table), ("m", MEAS)]
    single = {
        "visualType": vtype,
        "projections": {
            cat_role: [{"queryRef": "%s.%s" % (table, col)}],
            val_role: [{"queryRef": "%s.%s" % (MEAS, m)} for m in measures],
        },
        "prototypeQuery": proto(entities, selects),
        "objects": {
            # same reason as the cards: the format string already scales
            "valueAxis": [{"properties": _obj({"showAxisTitle": False,
                                               "labelDisplayUnits": 1})}],
            "categoryAxis": [{"properties": _obj({"showAxisTitle": False,
                                                  "fontSize": 9})}],
            "legend": [{"properties": _obj({"show": legend, "position": "Top",
                                            "fontSize": 9})}],
        },
        "vcObjects": {
            "title": [{"properties": _obj({"text": title, "show": True,
                                           "fontColor": INK, "fontSize": 11,
                                           "fontFamily": "Segoe UI Semibold"})}],
            "background": [{"properties": _obj({"color": PAPER,
                                                "transparency": 0})}],
            "border": [{"properties": _obj({"show": True, "color": "#E5E5E5"})}],
        },
        "drillFilterOtherVisuals": True,
    }
    if colors:
        single["objects"]["dataPoint"] = [
            {"properties": _obj({"fill": c}),
             "selector": {"metadata": "%s.%s" % (MEAS, measures[i])}}
            for i, c in enumerate(colors) if i < len(measures)
        ]
    return container(x, y, w, h, z, single)


def table_visual(x, y, w, h, z, cols: list[tuple[str, str]],
                 measures: list[str], title: str) -> dict:
    entities, selects, refs = [], [], []
    for i, (t, c) in enumerate(cols):
        a = "t%d" % i
        entities.append((a, t))
        selects.append(column_sel(t, c, a))
        refs.append({"queryRef": "%s.%s" % (t, c)})
    entities.append(("m", MEAS))
    for m in measures:
        selects.append(measure_sel(m))
        refs.append({"queryRef": "%s.%s" % (MEAS, m)})
    single = {
        "visualType": "tableEx",
        "projections": {"Values": refs},
        "prototypeQuery": proto(entities, selects),
        "objects": {
            "grid": [{"properties": _obj({"gridVertical": True,
                                          "gridVerticalColor": "#E5E5E5"})}],
            "columnHeaders": [{"properties": _obj({"fontColor": PAPER,
                                                   "backColor": INK,
                                                   "fontSize": 9})}],
            "values": [{"properties": _obj({"fontSize": 9})}],
        },
        "vcObjects": {
            "title": [{"properties": _obj({"text": title, "show": True,
                                           "fontColor": INK, "fontSize": 11,
                                           "fontFamily": "Segoe UI Semibold"})}],
            "background": [{"properties": _obj({"color": PAPER})}],
            "border": [{"properties": _obj({"show": True, "color": "#E5E5E5"})}],
        },
        "drillFilterOtherVisuals": True,
    }
    return container(x, y, w, h, z, single)


def slicer(x, y, w, h, z, table: str, col: str, title: str,
           mode: str = "Basic") -> dict:
    single = {
        "visualType": "slicer",
        "projections": {"Values": [{"queryRef": "%s.%s" % (table, col)}]},
        "prototypeQuery": proto([("s", table)], [column_sel(table, col, "s")]),
        "objects": {
            "data": [{"properties": _obj({"mode": mode})}],
        },
        "vcObjects": {
            "title": [{"properties": _obj({"text": title, "show": True,
                                           "fontColor": MUTED, "fontSize": 9})}],
            "background": [{"properties": _obj({"color": PAPER})}],
            "border": [{"properties": _obj({"show": True, "color": "#E5E5E5"})}],
        },
        "drillFilterOtherVisuals": True,
    }
    return container(x, y, w, h, z, single)


def textbox(x, y, w, h, z, runs: list[tuple[str, dict]]) -> dict:
    paragraphs = []
    for text, style in runs:
        paragraphs.append({
            "textRuns": [{"value": text, "textStyle": style}],
        })
    single = {
        "visualType": "textbox",
        "objects": {"general": [{"properties": {"paragraphs": paragraphs}}]},
        "vcObjects": {"background": [{"properties": _obj({"transparency": 100})}]},
        "drillFilterOtherVisuals": True,
    }
    return container(x, y, w, h, z, single)


H1 = {"fontSize": "18pt", "fontWeight": "600", "color": INK,
      "fontFamily": "Segoe UI"}
SUB = {"fontSize": "9pt", "color": MUTED, "fontFamily": "Segoe UI"}
NOTE = {"fontSize": "9pt", "color": MUTED, "fontFamily": "Segoe UI"}


def page(name: str, display: str, ordinal: int, visuals: list[dict]) -> dict:
    return {
        "name": name,
        "displayName": display,
        "ordinal": ordinal,
        "visualContainers": visuals,
        "config": json.dumps({
            "visibility": 0,
            "objects": {
                "background": [{"properties": _obj({"color": SURFACE,
                                                    "transparency": 0})}],
            },
        }),
        "filters": "[]",
        "displayOption": 1,
        "width": W,
        "height": H,
    }


# ------------------------------------------------------------------ pages
def page_commit() -> dict:
    v = []
    v.append(textbox(16, 12, 700, 56, 0, [
        ("What to commit, as a range", H1),
        ("Synthetic data. Demonstrates a method, not any company's business.", SUB),
    ]))
    x = 16
    for m, lbl in [("Open Backlog Material", "material against open backlog"),
                   ("Requirement P50", "expected next-month requirement"),
                   ("Material at Risk", "open orders past their promised date"),
                   ("Firm Share", "of next month already on booked orders")]:
        v.append(card(x, 78, 200, 92, 1, m, lbl))
        x += 208
    v.append(chart(16, 182, 848, 300, 2, "lineChart", ("dim_date", "year_month"),
                   ["Requirement P10", "Requirement P50", "Requirement P90"],
                   "Forward material requirement, with the stated 80% interval",
                   colors=[MUTED, ORANGE, MUTED]))
    v.append(slicer(880, 182, 184, 140, 3, "Realization Scenario",
                    "Realization Scenario", "Realization assumption"))
    v.append(card(880, 334, 184, 74, 4, "Material at Assumption",
                  "material at that rate"))
    v.append(card(880, 414, 184, 68, 4, "Exposure vs Full Commit",
                  "exposure the assumption controls"))
    v.append(textbox(16, 492, 848, 88, 5, [
        ("The grey lines are the range the total is expected to fall within "
         "eight times out of ten. The band widens across the horizon because "
         "the far months are mostly demand from orders nobody has placed yet.", NOTE),
        ("Move the slider to see what a realization assumption commits. The "
         "distance between the ends of that scale is the exposure it controls.", NOTE),
    ]))
    return page("pgCommit", "1  Commit position", 0, v)


def page_slip() -> dict:
    v = []
    v.append(textbox(16, 12, 800, 56, 0, [
        ("Where the uncertainty comes from", H1),
        ("Schedule slip by market, against the regional figure a plan would "
         "otherwise be set on.", SUB),
    ]))
    v.append(chart(16, 78, 620, 330, 1, "barChart", ("dim_market", "market"),
                   ["Slip P90", "Regional Blend P90"],
                   "P90 slip by market vs its regional blend, months",
                   colors=[ORANGE, MUTED]))
    v.append(table_visual(652, 78, 412, 330, 2,
                          [("dim_market", "region"), ("dim_market", "market")],
                          ["Slip P50", "Slip P90", "Regional Blend P90",
                           "Blend Gap"],
                          "The gap, per market"))
    v.append(chart(16, 420, 620, 240, 3, "columnChart",
                   ("dim_market", "market"), ["Order Value"],
                   "Booked value by market", colors=[INK], legend=False))
    v.append(textbox(652, 420, 412, 240, 4, [
        ("A region that holds one market is described by its own average. A "
         "region that holds two is not.", NOTE),
        ("APAC blends Australia and South East Asia together with India. Plan "
         "on the blended figure and it runs months early on one and months "
         "late on the other. India is flagged in the model as tracked "
         "separately for that reason, not because it is small.", NOTE),
        ("Slice any visual on this page and the P90 recomputes. It is a "
         "percentile over the filtered orders, not a stored value.", NOTE),
    ]))
    return page("pgSlip", "2  Slip by market", 1, v)


def page_coverage() -> dict:
    v = []
    v.append(textbox(16, 12, 800, 56, 0, [
        ("Was the forecast right", H1),
        ("Walk-forward test. The model is re-run at each past cutoff using only "
         "what was known then.", SUB),
    ]))
    x = 16
    for m, lbl in [("Coverage 80%", "of forecasts inside the stated 80%"),
                   ("Coverage 50%", "inside the stated 50%"),
                   ("Forecasts Scored", "walk-forward forecasts"),
                   ("Distinct Outcome Months", "distinct months they cover")]:
        v.append(card(x, 78, 200, 92, 1, m, lbl))
        x += 208
    v.append(chart(16, 182, 520, 290, 2, "columnChart",
                   ("fact_coverage", "months_ahead"), ["Coverage 80%"],
                   "Coverage by how far ahead the call was made",
                   colors=[ORANGE], legend=False))
    v.append(chart(552, 182, 512, 290, 3, "columnChart",
                   ("fact_coverage", "cutoff"), ["Mean Error"],
                   "Month +1 error by cutoff: high early, low late",
                   colors=[INK], legend=False))
    v.append(textbox(16, 484, 1048, 110, 4, [
        ("Both intervals sit above nominal, which is the safe direction. But "
         "those forecasts cover far fewer distinct months than the count "
         "suggests, because overlapping six-month windows score the same month "
         "up to six times. On that many effective observations neither gap "
         "clears chance.", NOTE),
        ("The right-hand chart is the result that does. Sorted by cutoff, the "
         "front-month error runs one way and then the other: early cutoffs "
         "forecast high, recent ones forecast low. That is drift, which a "
         "tracking signal catches. A wider interval would only mask it.", NOTE),
        ("No correction is fitted to these intervals. Conformal calibration "
         "assumes the scores are exchangeable, and scores sharing an outcome "
         "month are not.", NOTE),
    ]))
    return page("pgCoverage", "3  Interval coverage", 2, v)


def page_exceptions() -> dict:
    v = []
    v.append(textbox(16, 12, 800, 56, 0, [
        ("What actually moved", H1),
        ("Order-level detail behind every number on the other pages.", SUB),
    ]))
    v.append(slicer(16, 78, 200, 180, 1, "dim_market", "region", "Region"))
    v.append(slicer(16, 268, 200, 180, 1, "fact_orders", "status", "Status"))
    v.append(table_visual(232, 78, 832, 480, 2,
                          [("fact_orders", "order_id"),
                           ("dim_market", "market"),
                           ("fact_orders", "status")],
                          ["MW Booked", "Order Value", "Material Committed",
                           "Slip P90"],
                          "Orders, sliceable"))
    v.append(textbox(232, 570, 832, 70, 3, [
        ("Every aggregate on pages 1 to 3 resolves to these rows. The point of "
         "a model rather than a deck is that this stays connected: filter a "
         "region here and the commitment position on page 1 moves with it.", NOTE),
    ]))
    return page("pgOrders", "4  Order detail", 3, v)


def build() -> dict:
    return {
        "id": 0,
        "resourcePackages": [{
            "resourcePackage": {
                "name": "SharedResources",
                "type": 2,
                "items": [{"name": "CY24SU10", "path": "BaseThemes/CY24SU10.json",
                           "type": 202}],
                "disabled": False,
            }
        }],
        "sections": [page_commit(), page_slip(), page_coverage(),
                     page_exceptions()],
        "config": json.dumps({
            "version": "5.43",
            "themeCollection": {"baseTheme": {"name": "CY24SU10",
                                              "version": "5.55", "type": 2}},
            "activeSectionIndex": 0,
            "defaultDrillFilterOtherVisuals": True,
            "settings": {"useStylableVisualContainerHeader": True},
        }),
        "layoutOptimization": 0,
    }


def main() -> None:
    os.makedirs(REPORT_DIR, exist_ok=True)
    rep = build()
    path = os.path.join(REPORT_DIR, "report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
        f.write("\n")
    n = sum(len(s["visualContainers"]) for s in rep["sections"])
    print("wrote report.json")
    print("  pages:   %d" % len(rep["sections"]))
    for s in rep["sections"]:
        print("     %-22s %d visuals" % (s["displayName"],
                                         len(s["visualContainers"])))
    print("  visuals: %d" % n)


if __name__ == "__main__":
    main()
