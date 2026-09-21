"""Reshapes the simulation output into a star schema for Power BI.

The dashboard and the workbook both read the wide CSVs directly, which is fine
for a page that is generated once. A semantic model wants the opposite shape:
narrow fact tables at a stated grain, conformed dimensions, and no repeated
attributes on the facts.

Writes to ./pbi/data. Everything here is derived from ./data, so this is
regenerable and nothing is hand-maintained.

Grain of each fact table is stated in FACT_GRAIN below and asserted before
writing, because a fact table at the wrong grain double-counts silently.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "pbi", "data")

MATERIAL_RATE = 0.50

FACT_GRAIN = {
    "fact_orders": ["order_id"],
    "fact_forecast": ["month", "scope", "layer"],
    "fact_coverage": ["cutoff", "month"],
    "fact_lookback": ["market", "month"],
}


def check_grain(name: str, df: pd.DataFrame) -> None:
    keys = FACT_GRAIN[name]
    dupes = df.duplicated(subset=keys).sum()
    if dupes:
        raise SystemExit("%s: %d rows duplicate the stated grain %s"
                         % (name, dupes, keys))
    print("  %-16s %6d rows   grain %s" % (name, len(df), "+".join(keys)))


def main() -> None:
    os.makedirs(OUT, exist_ok=True)

    orders = pd.read_csv(os.path.join(SRC, "orders.csv"),
                         parse_dates=["booked_month", "expected_conversion",
                                      "actual_conversion"])
    mc = pd.read_csv(os.path.join(SRC, "mc_monthly.csv"), parse_dates=["month"])
    cov = pd.read_csv(os.path.join(SRC, "mc_coverage.csv"),
                      parse_dates=["cutoff", "month"])
    lb = pd.read_csv(os.path.join(SRC, "lookback_market.csv"), parse_dates=["month"])

    print("building star schema")

    # ---------------------------------------------------------------- dim_market
    dim_market = (orders[["market", "region", "edge_case"]]
                  .drop_duplicates()
                  .sort_values(["region", "market"])
                  .reset_index(drop=True))
    dim_market["market_key"] = dim_market.market
    # India is nested in APAC and behaves nothing like the rest of it. Carrying
    # the flag as a real column lets a visual split on it without a hardcoded
    # filter buried in a measure.
    dim_market["tracked_separately"] = np.where(dim_market.edge_case, "Yes", "No")
    dim_market = dim_market[["market_key", "market", "region", "tracked_separately"]]

    # ---------------------------------------------------------------- dim_date
    lo = min(orders.booked_month.min(), mc.month.min(), cov.cutoff.min())
    hi = max(orders.expected_conversion.max(), orders.actual_conversion.max(),
             mc.month.max())
    months = pd.date_range(lo, hi, freq="MS")
    dim_date = pd.DataFrame({"date": months})
    dim_date["date_key"] = dim_date.date.dt.strftime("%Y-%m-%d")
    dim_date["year"] = dim_date.date.dt.year
    dim_date["quarter"] = "Q" + dim_date.date.dt.quarter.astype(str)
    dim_date["month_no"] = dim_date.date.dt.month
    dim_date["month_name"] = dim_date.date.dt.strftime("%b")
    dim_date["year_month"] = dim_date.date.dt.strftime("%Y-%m")
    dim_date["fy_label"] = (dim_date.date.dt.strftime("%b ")
                            + dim_date.date.dt.strftime("%y"))
    # sort key, so "Jan 26" orders after "Dec 25" instead of alphabetically
    dim_date["year_month_sort"] = dim_date.year * 100 + dim_date.month_no

    # ---------------------------------------------------------------- fact_orders
    f_orders = orders.copy()
    f_orders["material_committed"] = f_orders.order_value * MATERIAL_RATE
    f_orders["booked_key"] = f_orders.booked_month.dt.strftime("%Y-%m-%d")
    f_orders["expected_key"] = f_orders.expected_conversion.dt.strftime("%Y-%m-%d")
    f_orders["actual_key"] = f_orders.actual_conversion.dt.strftime("%Y-%m-%d")
    f_orders["status"] = np.where(
        f_orders.cancelled, "Cancelled",
        np.where(f_orders.slip_months >= 2, "Slipped 2mo+", "On plan"))
    f_orders = f_orders[[
        "order_id", "market", "booked_key", "expected_key", "actual_key",
        "mw", "order_value", "material_committed", "promised_months",
        "slip_months", "cancelled", "status"]]

    # ---------------------------------------------------------------- fact_forecast
    f_fc = mc.rename(columns={"month": "month_dt"}).copy()
    f_fc["date_key"] = f_fc.month_dt.dt.strftime("%Y-%m-%d")
    f_fc["month"] = f_fc.month_dt
    keep = ["month", "date_key", "scope", "layer", "p10", "p25", "p50", "p75", "p90"]
    keep = [c for c in keep if c in f_fc.columns]
    f_fc = f_fc[keep]

    # ---------------------------------------------------------------- fact_coverage
    f_cov = cov.copy()
    f_cov["date_key"] = f_cov.month.dt.strftime("%Y-%m-%d")
    f_cov["cutoff_key"] = f_cov.cutoff.dt.strftime("%Y-%m-%d")
    f_cov["months_ahead"] = f_cov.offset
    f_cov["error"] = f_cov.actual - f_cov.p50
    f_cov["inside_80"] = ((f_cov.actual >= f_cov.p10)
                          & (f_cov.actual <= f_cov.p90)).astype(int)
    f_cov["inside_50"] = ((f_cov.actual >= f_cov.p25)
                          & (f_cov.actual <= f_cov.p75)).astype(int)
    f_cov["direction"] = np.where(f_cov.error < 0, "Forecast high", "Forecast low")
    f_cov = f_cov[["cutoff", "month", "cutoff_key", "date_key", "months_ahead",
                   "actual", "p10", "p25", "p50", "p75", "p90",
                   "error", "inside_80", "inside_50", "direction"]]

    # ---------------------------------------------------------------- fact_lookback
    f_lb = lb.rename(columns={"market": "market_key"}).copy()
    f_lb["date_key"] = f_lb.month.dt.strftime("%Y-%m-%d")
    f_lb["market"] = f_lb.market_key
    f_lb = f_lb[["market", "month", "date_key", "expected_mw", "actual_mw",
                 "realization", "bias_mw"]]

    facts = {
        "fact_orders": f_orders,
        "fact_forecast": f_fc,
        "fact_coverage": f_cov,
        "fact_lookback": f_lb,
    }
    for name, df in facts.items():
        check_grain(name, df)

    print("  %-16s %6d rows" % ("dim_market", len(dim_market)))
    print("  %-16s %6d rows" % ("dim_date", len(dim_date)))

    # referential integrity: every fact key must exist in the dimension, or
    # Power BI silently creates a blank row and totals stop tying
    valid_dates = set(dim_date.date_key)
    problems = 0
    for name, df in facts.items():
        for col in [c for c in df.columns if c.endswith("_key") and "market" not in c]:
            missing = set(df[col].dropna()) - valid_dates
            if missing:
                problems += 1
                print("  *** %s.%s has %d keys not in dim_date, e.g. %s"
                      % (name, col, len(missing), sorted(missing)[:3]))
    bad_mkt = set(f_orders.market) - set(dim_market.market_key)
    if bad_mkt:
        problems += 1
        print("  *** fact_orders.market not in dim_market:", bad_mkt)
    if problems:
        raise SystemExit("referential integrity failed")
    print("  referential integrity: every fact key resolves")

    dim_market.to_csv(os.path.join(OUT, "dim_market.csv"), index=False)
    dim_date.drop(columns=["date"]).assign(
        date=dim_date.date.dt.strftime("%Y-%m-%d")).to_csv(
        os.path.join(OUT, "dim_date.csv"), index=False)
    for name, df in facts.items():
        out = df.copy()
        for c in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[c]):
                out[c] = out[c].dt.strftime("%Y-%m-%d")
        out.to_csv(os.path.join(OUT, name + ".csv"), index=False)

    print()
    print("wrote 6 tables to", OUT)


if __name__ == "__main__":
    main()
