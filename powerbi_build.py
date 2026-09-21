"""Generates the Power BI project (.pbip) from the star schema.

.pbip rather than .pbix on purpose: it is the open, text-based project format,
so the model and the report are readable JSON in git rather than a binary blob.
Power BI Desktop opens the .pbip directly.

Everything here is generated. Nothing in ./pbi is hand-edited, so a change to
the simulation flows through powerbi_export.py and this file without anyone
reconciling a saved report by hand.

Run after powerbi_export.py.
"""

from __future__ import annotations

import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PBI = os.path.join(HERE, "pbi")
NAME = "Nextpower SOP"
# "Measures" is reserved by Power BI and rejects the model on load
MEASURE_TABLE = "S&OP Metrics"
MODEL_DIR = os.path.join(PBI, NAME + ".SemanticModel")
REPORT_DIR = os.path.join(PBI, NAME + ".Report")
DATA_DIR = os.path.join(PBI, "data")

ORANGE = "#FE5000"
INK = "#111827"
MUTED = "#737373"
SURFACE = "#F5F5F5"
LINE = "#D4D4D4"


# --------------------------------------------------------------------- M queries
def m_query(table: str, cols: list[tuple[str, str]]) -> str:
    """Power Query for one CSV. Types are declared explicitly rather than left
    to type detection, because a column that arrives as text silently breaks
    every relationship and measure built on it.

    The folder is written in literally rather than read from a shared parameter.
    A DataFolder parameter is the tidier form and it is what I built first, but
    Power BI reported "a cyclic reference was encountered" and blocked every
    query that used it. Since this whole project is generated, re-running
    powerbi_build.py on another machine produces correct paths for that machine,
    which is the same portability a parameter would have bought.
    """
    casts = ", ".join('{"%s", %s}' % (c, t) for c, t in cols)
    # one backslash, not two. M does not treat backslash as an escape character
    # inside a string literal, so "\\name.csv" is a literal double separator and
    # File.Contents will not resolve it.
    return (
        'let\n'
        '    Source = Csv.Document(\n'
        '        File.Contents("%s\\%s.csv"),\n'
        '        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]\n'
        '    ),\n'
        '    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),\n'
        '    Typed = Table.TransformColumnTypes(Headers, {%s})\n'
        'in\n'
        '    Typed' % (DATA_DIR, table, casts)
    )


TXT, NUM, INT, DATE, BOOL = "type text", "type number", "Int64.Type", "type date", "type logical"

TABLES = {
    "dim_date": [
        ("date_key", DATE), ("year", INT), ("quarter", TXT), ("month_no", INT),
        ("month_name", TXT), ("year_month", TXT), ("fy_label", TXT),
        ("year_month_sort", INT), ("date", DATE),
    ],
    "dim_market": [
        ("market_key", TXT), ("market", TXT), ("region", TXT),
        ("tracked_separately", TXT),
    ],
    "fact_orders": [
        ("order_id", TXT), ("market", TXT), ("booked_key", DATE),
        ("expected_key", DATE), ("actual_key", DATE), ("mw", NUM),
        ("order_value", NUM), ("material_committed", NUM),
        ("promised_months", INT), ("slip_months", NUM), ("cancelled", BOOL),
        ("status", TXT),
    ],
    "fact_forecast": [
        ("month", DATE), ("date_key", DATE), ("scope", TXT), ("layer", TXT),
        ("p10", NUM), ("p25", NUM), ("p50", NUM), ("p75", NUM), ("p90", NUM),
    ],
    "fact_coverage": [
        ("cutoff", DATE), ("month", DATE), ("cutoff_key", DATE), ("date_key", DATE),
        ("months_ahead", INT), ("actual", NUM), ("p10", NUM), ("p25", NUM),
        ("p50", NUM), ("p75", NUM), ("p90", NUM), ("error", NUM),
        ("inside_80", INT), ("inside_50", INT), ("direction", TXT),
    ],
    "fact_lookback": [
        ("market", TXT), ("month", DATE), ("date_key", DATE),
        ("expected_mw", NUM), ("actual_mw", NUM), ("realization", NUM),
        ("bias_mw", NUM),
    ],
}

DTYPE = {TXT: "string", NUM: "double", INT: "int64", DATE: "dateTime",
         BOOL: "boolean"}


# --------------------------------------------------------------------- measures
# format strings kept next to the definition, because an unformatted measure is
# the fastest way to make a real model look like a prototype
M = [
    # -- volume ---------------------------------------------------------------
    ("Orders", "COUNTROWS ( fact_orders )", "#,0", "Volume",
     "Count of booked projects in context."),
    ("MW Booked", "SUM ( fact_orders[mw] )", "#,0.0", "Volume",
     "Nameplate MW on booked orders."),
    ("Order Value", "SUM ( fact_orders[order_value] )", "\\$#,0,,\"M\"", "Volume",
     "Booked order value."),
    ("Material Committed",
     "SUM ( fact_orders[material_committed] )", "\\$#,0,,\"M\"", "Volume",
     "Order value times the material rate from the case study."),

    # -- backlog, as-of -------------------------------------------------------
    # SUMX over an explicit FILTER rather than CALCULATE boolean predicates.
    # The predicate form returned blank: a CALCULATE filter argument has to be
    # a simple single-column predicate, and the OR across two conditions on
    # actual_key is not one. FILTER keeps market context from dim_market while
    # the as-of logic is applied per row.
    ("Open Backlog $",
     "VAR LastBooked =\n"
     "    CALCULATE ( MAX ( fact_orders[booked_key] ), REMOVEFILTERS ( dim_date ) )\n"
     "VAR AsOf = MIN ( MAX ( dim_date[date] ), LastBooked )\n"
     "RETURN\n"
     "CALCULATE (\n"
     "    SUMX (\n"
     "        FILTER (\n"
     "            fact_orders,\n"
     "            fact_orders[booked_key] <= AsOf\n"
     "                && NOT fact_orders[cancelled]\n"
     "                && ( ISBLANK ( fact_orders[actual_key] )\n"
     "                     || fact_orders[actual_key] > AsOf )\n"
     "        ),\n"
     "        fact_orders[order_value]\n"
     "    ),\n"
     "    REMOVEFILTERS ( dim_date )\n"
     ")", "\\$#,0,,\"M\"", "Backlog",
     "Booked on or before the date, not cancelled, not yet converted. Cancelled "
     "orders are excluded from both sides; leaving them in the booked side "
     "overstates backlog permanently, since they never convert. The as-of date "
     "clamps to the last booked month: dim_date has to run out to 2028 to cover "
     "conversions, and an unfiltered MAX over it asked for backlog as of a date "
     "two years past the end of the book, which nothing satisfies."),
    ("Open Backlog Material",
     "[Open Backlog $] * 0.5", "\\$#,0,,\"M\"", "Backlog",
     "Material value of open backlog."),
    ("Material at Risk",
     "VAR LastBooked =\n"
     "    CALCULATE ( MAX ( fact_orders[booked_key] ), REMOVEFILTERS ( dim_date ) )\n"
     "VAR AsOf = MIN ( MAX ( dim_date[date] ), LastBooked )\n"
     "RETURN\n"
     "CALCULATE (\n"
     "    SUMX (\n"
     "        FILTER (\n"
     "            fact_orders,\n"
     "            fact_orders[booked_key] <= AsOf\n"
     "                && NOT fact_orders[cancelled]\n"
     "                && ( ISBLANK ( fact_orders[actual_key] )\n"
     "                     || fact_orders[actual_key] > AsOf )\n"
     "                && fact_orders[expected_key] <= AsOf\n"
     "        ),\n"
     "        fact_orders[material_committed]\n"
     "    ),\n"
     "    REMOVEFILTERS ( dim_date )\n"
     ")", "\\$#,0,,\"M\"", "Backlog",
     "Open orders already past their promised date. Filtered on the promised "
     "date, not on realised slip: slip is only knowable after the fact, and a "
     "KPI built on it reports something no planner could have produced."),

    # -- flow -----------------------------------------------------------------
    ("Converted $",
     "CALCULATE (\n"
     "    SUM ( fact_orders[order_value] ),\n"
     "    USERELATIONSHIP ( fact_orders[actual_key], dim_date[date] ),\n"
     "    fact_orders[cancelled] = FALSE\n"
     ")", "\\$#,0,,\"M\"", "Flow",
     "Order value converting in the month, using the actual conversion date."),
    ("Book to Bill",
     "DIVIDE ( [Order Value], [Converted $] )", "0.00", "Flow",
     "Booked over converted. Above 1.0 the book is growing."),

    # -- forecast -------------------------------------------------------------
    ("Requirement P50",
     "CALCULATE ( SUM ( fact_forecast[p50] ),\n"
     "    fact_forecast[scope] = \"TOTAL\", fact_forecast[layer] = \"all\" )",
     "\\$#,0,,\"M\"", "Forecast", "Median forward material requirement."),
    ("Requirement P10",
     "CALCULATE ( SUM ( fact_forecast[p10] ),\n"
     "    fact_forecast[scope] = \"TOTAL\", fact_forecast[layer] = \"all\" )",
     "\\$#,0,,\"M\"", "Forecast", "Low end of the stated 80% interval."),
    ("Requirement P90",
     "CALCULATE ( SUM ( fact_forecast[p90] ),\n"
     "    fact_forecast[scope] = \"TOTAL\", fact_forecast[layer] = \"all\" )",
     "\\$#,0,,\"M\"", "Forecast", "High end of the stated 80% interval."),
    ("Interval Width",
     "[Requirement P90] - [Requirement P10]", "\\$#,0,,\"M\"", "Forecast",
     "Width of the stated 80% interval."),
    ("Firm Share",
     "VAR Firm =\n"
     "    CALCULATE ( SUM ( fact_forecast[p50] ),\n"
     "        fact_forecast[scope] = \"TOTAL\", fact_forecast[layer] = \"firm\" )\n"
     "RETURN DIVIDE ( Firm, [Requirement P50] )", "0%", "Forecast",
     "Share of the month's requirement already on booked orders."),

    # -- the quantile point ---------------------------------------------------
    ("Sum of Regional P90",
     "CALCULATE (\n"
     "    SUM ( fact_forecast[p90] ),\n"
     "    REMOVEFILTERS ( fact_forecast[scope] ),\n"
     "    fact_forecast[scope] IN { \"North America\", \"EMEA\", \"APAC\", \"LATAM\" },\n"
     "    fact_forecast[layer] = \"all\"\n"
     ")", "\\$#,0,,\"M\"", "Forecast",
     "Adding the four regional P90s together."),
    ("Quantile Overstatement %",
     "DIVIDE ( [Sum of Regional P90] - [Requirement P90], [Requirement P90] )",
     "0.0%", "Forecast",
     "How much adding regional worst cases overstates the portfolio worst case. "
     "Quantiles do not add: the regions do not all run late in the same month. "
     "The gap is material that would be bought and not needed."),

    # -- slip distribution ----------------------------------------------------
    # PERCENTILEX over the filtered fact rather than a stored percentile, so the
    # number responds to whatever the user has sliced to
    ("Slip P50",
     "PERCENTILEX.INC (\n"
     "    FILTER ( fact_orders, fact_orders[cancelled] = FALSE ),\n"
     "    fact_orders[slip_months], 0.5\n"
     ")", "0.0", "Slip", "Median schedule slip, in months."),
    ("Slip P90",
     "PERCENTILEX.INC (\n"
     "    FILTER ( fact_orders, fact_orders[cancelled] = FALSE ),\n"
     "    fact_orders[slip_months], 0.9\n"
     ")", "0.0", "Slip",
     "90th percentile schedule slip. The number a commitment horizon is set on."),
    # the region has to be re-applied after the market filter is dropped.
    # REMOVEFILTERS(dim_market[market]) alone leaves no region in context and
    # returns the global P90 for every row, which looked plausible and was not.
    ("Regional Blend P90",
     "VAR Rgn = SELECTEDVALUE ( dim_market[region] )\n"
     "RETURN\n"
     "CALCULATE (\n"
     "    [Slip P90],\n"
     "    REMOVEFILTERS ( dim_market ),\n"
     "    dim_market[region] = Rgn\n"
     ")",
     "0.0", "Slip",
     "The same P90 computed at region level. Where a region holds more than one "
     "market it describes neither of them: APAC blends Australia and India into "
     "a figure that runs early on one and late on the other."),
    ("Blend Gap",
     "[Slip P90] - [Regional Blend P90]", "+0.0;-0.0", "Slip",
     "Months between a market's own P90 and the regional figure it would be "
     "planned on."),

    # -- realization ----------------------------------------------------------
    ("Expected MW", "SUM ( fact_lookback[expected_mw] )", "#,0.0", "Realization",
     "MW the order book expected to convert."),
    ("Actual MW", "SUM ( fact_lookback[actual_mw] )", "#,0.0", "Realization",
     "MW that actually converted."),
    ("Realization Rate",
     "DIVIDE ( [Actual MW], [Expected MW] )", "0.0%", "Realization",
     "Measured, not assumed. This is the number a planning cycle usually picks."),
    ("Bias MW", "SUM ( fact_lookback[bias_mw] )", "+#,0.0;-#,0.0", "Realization",
     "Actual minus expected, in MW."),
    ("Cumulative Bias MW",
     "CALCULATE (\n"
     "    [Bias MW],\n"
     "    dim_date[date] <= MAX ( dim_date[date] ),\n"
     "    REMOVEFILTERS ( dim_date )\n"
     ")", "+#,0.0;-#,0.0", "Realization",
     "Running total of bias. A region with persistent drift shows here as a "
     "line that does not return to zero."),

    # -- what-if --------------------------------------------------------------
    ("Realization Assumption",
     "SELECTEDVALUE ( 'Realization Scenario'[Realization Scenario], 0.85 )",
     "0%", "Scenario", "The rate selected on the slider."),
    ("Material at Assumption",
     "[Open Backlog Material] * [Realization Assumption]", "\\$#,0,,\"M\"",
     "Scenario",
     "Material implied by the chosen realization rate."),
    ("Exposure vs Full Commit",
     "[Open Backlog Material] - [Material at Assumption]", "\\$#,0,,\"M\"",
     "Scenario",
     "Distance between committing against the whole book and committing against "
     "the assumption. This is the exposure the assumption controls."),

    # -- coverage -------------------------------------------------------------
    ("Forecasts Scored", "COUNTROWS ( fact_coverage )", "#,0", "Coverage",
     "Walk-forward forecasts scored against what the month turned out to be."),
    ("Distinct Outcome Months",
     "DISTINCTCOUNT ( fact_coverage[month] )", "#,0", "Coverage",
     "Overlapping six-month windows score the same month up to six times, so "
     "this is well below the number of forecasts. It is the effective sample "
     "size, and the reason no correction is fitted to the interval."),
    ("Coverage 80%",
     "DIVIDE ( SUM ( fact_coverage[inside_80] ), [Forecasts Scored] )",
     "0.0%", "Coverage", "Share of forecasts the stated 80% interval contained."),
    ("Coverage 50%",
     "DIVIDE ( SUM ( fact_coverage[inside_50] ), [Forecasts Scored] )",
     "0.0%", "Coverage", "Share the stated 50% interval contained."),
    ("Misses High",
     "CALCULATE ( COUNTROWS ( fact_coverage ),\n"
     "    FILTER ( fact_coverage, fact_coverage[actual] > fact_coverage[p90] ) )",
     "#,0", "Coverage", "Outcomes above the interval."),
    ("Misses Low",
     "CALCULATE ( COUNTROWS ( fact_coverage ),\n"
     "    FILTER ( fact_coverage, fact_coverage[actual] < fact_coverage[p10] ) )",
     "#,0", "Coverage", "Outcomes below the interval."),
    ("Median Error",
     "MEDIAN ( fact_coverage[error] )", "\\$#,0,,\"M\"", "Coverage",
     "Median of actual minus P50. Near zero can hide a front end that runs low "
     "early and high late, because the halves cancel."),
    ("Mean Error", "AVERAGE ( fact_coverage[error] )", "\\$#,0,,\"M\"", "Coverage",
     "Mean of actual minus P50."),
]


def measure(name, expr, fmt, folder, desc):
    return {
        "name": name,
        "expression": expr.split("\n") if "\n" in expr else expr,
        "formatString": fmt,
        "displayFolder": folder,
        "description": desc,
        "lineageTag": str(uuid.uuid4()),
    }


def column(name, dtype, fmt=None, hidden=False, sort_by=None, summarize=None):
    c = {
        "name": name,
        "dataType": dtype,
        "sourceColumn": name,
        "lineageTag": str(uuid.uuid4()),
        "summarizeBy": summarize or ("sum" if dtype in ("double", "int64") else "none"),
    }
    if dtype == "dateTime":
        c["formatString"] = "yyyy-mm-dd"
    if fmt:
        c["formatString"] = fmt
    if hidden:
        c["isHidden"] = True
    if sort_by:
        c["sortByColumn"] = sort_by
    return c


def build_model() -> dict:
    tables = []

    for tname, cols in TABLES.items():
        tcols = []
        for cname, mtype in cols:
            hidden = cname.endswith("_key") and cname != "date_key"
            sort_by = None
            if tname == "dim_date" and cname == "year_month":
                sort_by = "year_month_sort"
            tcols.append(column(cname, DTYPE[mtype], hidden=hidden, sort_by=sort_by))
        t = {
            "name": tname,
            "lineageTag": str(uuid.uuid4()),
            "columns": tcols,
            "partitions": [{
                "name": tname,
                "mode": "import",
                "source": {"type": "m", "expression": m_query(tname, cols).split("\n")},
            }],
        }
        if tname == "dim_date":
            t["dataCategory"] = "Time"
        if tname.startswith("fact_"):
            t["isHidden"] = False
        tables.append(t)

    # what-if parameter: a calculated table, which is how Power BI itself
    # implements the slider
    tables.append({
        "name": "Realization Scenario",
        "lineageTag": str(uuid.uuid4()),
        "columns": [{
            "name": "Realization Scenario",
            "dataType": "double",
            # isNameInferred / isDataTypeInferred are only legal once the column
            # is declared a calculatedTableColumn; without the type Power BI
            # reads them as unrecognised properties and refuses the model
            "type": "calculatedTableColumn",
            "isNameInferred": True,
            "isDataTypeInferred": True,
            "sourceColumn": "[Realization Scenario]",
            "formatString": "0%",
            "lineageTag": str(uuid.uuid4()),
            "summarizeBy": "none",
            "annotations": [{"name": "SummarizationSetBy", "value": "Automatic"}],
        }],
        "partitions": [{
            "name": "Realization Scenario",
            "mode": "import",
            # GENERATESERIES names its column "Value", so the declared column
            # name never matched and every reference to it failed to resolve.
            # SELECTCOLUMNS renames it at source.
            "source": {"type": "calculated",
                       "expression": 'SELECTCOLUMNS (\n'
                                     '    GENERATESERIES ( 0.55, 1, 0.05 ),\n'
                                     '    "Realization Scenario", [Value]\n'
                                     ')'},
        }],
        "annotations": [{"name": "PBI_Id", "value": uuid.uuid4().hex}],
    })

    # measures live on a dedicated table so the field list reads as a model
    # rather than as a pile of columns. Not named "Measures": Power BI reserves
    # that and rejects the whole model with "Unsupported Table name".
    tables.append({
        "name": MEASURE_TABLE,
        "lineageTag": str(uuid.uuid4()),
        "columns": [{
            "name": "_placeholder",
            "dataType": "string",
            "type": "calculatedTableColumn",
            "isNameInferred": True,
            "isDataTypeInferred": True,
            "sourceColumn": "[_placeholder]",
            "isHidden": True,
            "lineageTag": str(uuid.uuid4()),
            "summarizeBy": "none",
        }],
        "partitions": [{
            "name": MEASURE_TABLE,
            "mode": "import",
            "source": {"type": "calculated",
                       "expression": 'ROW ( "_placeholder", BLANK () )'},
        }],
        "measures": [measure(*m) for m in M],
    })

    def rel(name, ft, fc, tt, tc, active=True):
        return {
            "name": name,
            "fromTable": ft, "fromColumn": fc,
            "toTable": tt, "toColumn": tc,
            "isActive": active,
            "crossFilteringBehavior": "oneDirection",
        }

    relationships = [
        rel("orders_booked_date", "fact_orders", "booked_key", "dim_date", "date"),
        rel("orders_expected_date", "fact_orders", "expected_key", "dim_date",
            "date", active=False),
        rel("orders_actual_date", "fact_orders", "actual_key", "dim_date",
            "date", active=False),
        rel("orders_market", "fact_orders", "market", "dim_market", "market_key"),
        rel("forecast_date", "fact_forecast", "date_key", "dim_date", "date"),
        rel("coverage_date", "fact_coverage", "date_key", "dim_date", "date"),
        rel("coverage_cutoff", "fact_coverage", "cutoff_key", "dim_date", "date",
            active=False),
        rel("lookback_date", "fact_lookback", "date_key", "dim_date", "date"),
        rel("lookback_market", "fact_lookback", "market", "dim_market", "market_key"),
    ]
    for r in relationships:
        r["fromCardinality"] = "many"
        r["toCardinality"] = "one"

    # no shared expressions: the folder is inlined in each query, see m_query
    expressions = []

    return {
        "name": "SemanticModel",
        "compatibilityLevel": 1567,
        "model": {
            "culture": "en-US",
            "collation": "Latin1_General_100_BIN2_UTF8",
            "dataAccessOptions": {
                "legacyRedirects": True,
                "returnErrorValuesAsNull": True,
            },
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "tables": tables,
            "relationships": relationships,
            "expressions": expressions,
            "annotations": [
                {"name": "PBI_QueryOrder",
                 "value": json.dumps(list(TABLES.keys()))},
                {"name": "PBI_ProTooling", "value": '["DevMode"]'},
            ],
        },
    }


def platform(kind: str, display: str) -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/"
                   "gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": kind, "displayName": display},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }


def write(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)
        else:
            json.dump(obj, f, indent=2)
            f.write("\n")


def main() -> None:
    os.makedirs(MODEL_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    write(os.path.join(PBI, NAME + ".pbip"), {
        "version": "1.0",
        "artifacts": [{"report": {"path": NAME + ".Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    write(os.path.join(MODEL_DIR, ".platform"), platform("SemanticModel", NAME))
    write(os.path.join(MODEL_DIR, "definition.pbism"),
          {"version": "1.0", "settings": {}})
    write(os.path.join(MODEL_DIR, "model.bim"), build_model())

    write(os.path.join(REPORT_DIR, ".platform"), platform("Report", NAME))
    write(os.path.join(REPORT_DIR, "definition.pbir"), {
        "version": "1.0",
        "datasetReference": {"byPath": {"path": "../" + NAME + ".SemanticModel"}},
    })

    model = build_model()
    n_meas = len(model["model"]["tables"][-1]["measures"])
    print("wrote %s.pbip" % NAME)
    print("  tables:        %d" % len(model["model"]["tables"]))
    print("  relationships: %d (%d inactive, for role-playing dates)"
          % (len(model["model"]["relationships"]),
             sum(1 for r in model["model"]["relationships"] if not r["isActive"])))
    print("  measures:      %d" % n_meas)
    print("  data folder:   %s" % DATA_DIR)


if __name__ == "__main__":
    main()
