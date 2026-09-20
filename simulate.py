"""
Nextpower S&OP demonstration - project-level demand simulation.

SYNTHETIC DATA. Region names and the US/Rest-of-World revenue weighting are taken
from public Nextracker filings and public role titles. Everything else, especially
the project slippage behaviour, is invented to demonstrate a method. None of it is
a representation of Nextpower's actual business.

Generates 24 months of project-level bookings across five regions, each with its own
schedule-slippage distribution, then derives the series a monthly S&OP cycle needs:
committed material, coverage against the steel commitment horizon, realised vs
expected conversion, and per-order exceptions.

Outputs (CSV, written next to this file in ./data):
    orders.csv      one row per booked project
    monthly.csv     region x month aggregates
    lookback.csv    expected vs actual conversion, by region and month
    exceptions.csv  orders whose expected conversion date moved
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd

RNG = np.random.default_rng(20260920)

START = pd.Timestamp("2024-01-01")
MONTHS = 24
HORIZON_WEEKS = 18          # midpoint of the 16-20 week steel commitment window
MATERIAL_RATE = 0.50        # see note below

# Four top-level regions, each matching a role title found publicly at the company:
# North America (Sr. Director Operations), EMEA (Director), LATAM, APAC (VP, SCM).
# North America ~69% of revenue per Nextracker FY2025 10-K (US $2,031.6M vs
# Rest of World $927.6M). The rest is split plausibly.
#
# Markets sit inside regions. MENA belongs to EMEA, not beside it. India belongs to
# APAC. India is carried as its own market because its schedule behaviour is an
# outlier, which is the point: a regional average hides it.
REGION_SHARE = {
    "North America": 0.69,
    "EMEA":          0.15,
    "APAC":          0.09,
    "LATAM":         0.07,
}

MARKETS = {
    # market            region            weight asp      slip_mean slip_sd cancel
    "US / Canada":  dict(region="North America", w=1.00, asp=83_000, slip_mean=0.6, slip_sd=1.2, cancel=0.02),
    "Europe":       dict(region="EMEA",          w=0.62, asp=88_000, slip_mean=1.2, slip_sd=1.8, cancel=0.03),
    "MENA":         dict(region="EMEA",          w=0.38, asp=81_000, slip_mean=2.2, slip_sd=2.8, cancel=0.06),
    "India":        dict(region="APAC",          w=0.65, asp=71_000, slip_mean=5.2, slip_sd=6.4, cancel=0.11),
    "Australia/SEA": dict(region="APAC",         w=0.35, asp=84_000, slip_mean=1.5, slip_sd=2.0, cancel=0.03),
    "LATAM":        dict(region="LATAM",         w=1.00, asp=79_000, slip_mean=2.2, slip_sd=3.0, cancel=0.05),
}

# markets whose behaviour is flagged as an outlier rather than folded into the
# regional average
EDGE_CASE_MARKETS = {"India"}

REGIONS = list(REGION_SHARE)

# Mean projects booked per month, scaled by market share of total.
BASE_PROJECTS_PER_MONTH = 34


def month_index(i: int) -> pd.Timestamp:
    return START + pd.DateOffset(months=i)


def build_orders() -> pd.DataFrame:
    rows = []
    oid = 0
    for i in range(MONTHS):
        booked = month_index(i)
        # mild seasonality plus a demand step-change in the final six months,
        # so the lookback has something to detect
        seasonal = 1.0 + 0.18 * np.sin(2 * np.pi * (i % 12) / 12)
        step = 1.35 if i >= MONTHS - 6 else 1.0
        for market, cfg in MARKETS.items():
            region = cfg["region"]
            share = REGION_SHARE[region] * cfg["w"]
            lam = BASE_PROJECTS_PER_MONTH * share * seasonal * step
            n = RNG.poisson(lam)
            for _ in range(n):
                oid += 1
                mw = float(np.round(RNG.lognormal(mean=2.6, sigma=0.75), 1))
                value = mw * cfg["asp"]

                # Promised lead time from booking to delivery, in months. Drawn from
                # a gamma rather than a uniform: a uniform 5-11 puts a hard floor at
                # month 5, which makes "months fully firm" a readout of the parameter
                # rather than a property of the book.
                promised = int(np.clip(round(RNG.gamma(4.0, 2.0)), 5, 24))
                expected = booked + pd.DateOffset(months=promised)

                # schedule slip, in months. Gamma-shaped: mostly small, long right tail.
                # Region sets the scale, which is the object the whole demo turns on.
                if cfg["slip_sd"] > 0:
                    shape = max((cfg["slip_mean"] / cfg["slip_sd"]) ** 2, 0.25)
                    scale = cfg["slip_sd"] ** 2 / max(cfg["slip_mean"], 1e-6)
                    slip = float(RNG.gamma(shape, scale))
                else:
                    slip = 0.0
                # Ceiling set well clear of the tail. At 18 the India P90 landed on
                # the bound itself, which makes a reported quantile a readout of the
                # clip rather than of the distribution.
                slip = float(np.clip(slip, 0, 36))

                cancelled = bool(RNG.random() < cfg["cancel"])
                actual = expected + pd.DateOffset(months=int(round(slip)))

                rows.append(dict(
                    order_id=f"NX-{oid:05d}",
                    region=region,
                    market=market,
                    edge_case=market in EDGE_CASE_MARKETS,
                    booked_month=booked,
                    mw=mw,
                    order_value=value,
                    material_committed=value * MATERIAL_RATE,
                    promised_months=promised,
                    expected_conversion=expected,
                    slip_months=round(slip, 2),
                    actual_conversion=pd.NaT if cancelled else actual,
                    cancelled=cancelled,
                ))
    df = pd.DataFrame(rows)
    df["expected_conversion"] = df["expected_conversion"].dt.to_period("M").dt.to_timestamp()
    df["actual_conversion"] = df["actual_conversion"].dt.to_period("M").dt.to_timestamp()
    return df


def build_monthly(orders: pd.DataFrame, by: str = "region") -> pd.DataFrame:
    months = [month_index(i) for i in range(MONTHS)]
    out = []
    for region in orders[by].unique():
        r = orders[orders[by] == region]
        for m in months:
            booked = r[r.booked_month == m]
            converted = r[(r.actual_conversion == m) & (~r.cancelled)]
            # open backlog: booked on or before m, not yet converted, not cancelled
            open_bl = r[(r.booked_month <= m) &
                        (~r.cancelled) &
                        ((r.actual_conversion.isna()) | (r.actual_conversion > m))]
            # what the order book says should convert inside the commitment horizon
            horizon_end = m + pd.Timedelta(weeks=HORIZON_WEEKS)
            in_horizon = open_bl[(open_bl.expected_conversion > m) &
                                 (open_bl.expected_conversion <= horizon_end)]
            out.append({
                by: region,
                "month": m,
                "orders_booked": len(booked),
                "mw_booked": booked.mw.sum(),
                "value_booked": booked.order_value.sum(),
                "mw_converted": converted.mw.sum(),
                "value_converted": converted.order_value.sum(),
                "backlog_value": open_bl.order_value.sum(),
                "backlog_mw": open_bl.mw.sum(),
                "committed_material": open_bl.material_committed.sum(),
                "material_in_horizon": in_horizon.material_committed.sum(),
                "mw_in_horizon": in_horizon.mw.sum(),
            })
    df = pd.DataFrame(out)
    df["book_to_bill"] = np.where(df.value_converted > 0,
                                  df.value_booked / df.value_converted, np.nan)
    return df


def build_lookback(orders: pd.DataFrame, by: str = "region") -> pd.DataFrame:
    """Expected vs actual conversion, by month. This is the mechanism that turns a
    chosen realization rate into a measured one with a known bias."""
    months = [month_index(i) for i in range(MONTHS)]
    out = []
    for key in orders[by].unique():
        r = orders[orders[by] == key]
        for m in months:
            exp = r[r.expected_conversion == m]
            act = r[(r.actual_conversion == m) & (~r.cancelled)]
            exp_mw, act_mw = exp.mw.sum(), act.mw.sum()
            out.append({
                by: key,
                "month": m,
                "expected_mw": exp_mw,
                "actual_mw": act_mw,
                "realization": (act_mw / exp_mw) if exp_mw > 0 else np.nan,
                "bias_mw": act_mw - exp_mw,
            })
    return pd.DataFrame(out)


def build_exceptions(orders: pd.DataFrame, threshold_months: float = 2.0) -> pd.DataFrame:
    e = orders[(orders.slip_months >= threshold_months) | (orders.cancelled)].copy()
    e["reason"] = np.where(e.cancelled, "cancelled", "schedule slip")
    e["material_at_risk"] = e.material_committed
    cols = ["order_id", "region", "market", "booked_month", "mw", "order_value",
            "expected_conversion", "actual_conversion", "slip_months",
            "reason", "material_at_risk"]
    return e[cols].sort_values("material_at_risk", ascending=False)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    outdir = os.path.join(here, "data")
    os.makedirs(outdir, exist_ok=True)

    orders = build_orders()
    monthly = build_monthly(orders, by="region")
    monthly_mkt = build_monthly(orders, by="market")
    lookback = build_lookback(orders, by="region")
    lookback_mkt = build_lookback(orders, by="market")
    exceptions = build_exceptions(orders)

    orders.to_csv(os.path.join(outdir, "orders.csv"), index=False)
    monthly.to_csv(os.path.join(outdir, "monthly.csv"), index=False)
    monthly_mkt.to_csv(os.path.join(outdir, "monthly_market.csv"), index=False)
    lookback.to_csv(os.path.join(outdir, "lookback.csv"), index=False)
    lookback_mkt.to_csv(os.path.join(outdir, "lookback_market.csv"), index=False)
    exceptions.to_csv(os.path.join(outdir, "exceptions.csv"), index=False)

    print("orders     %6d rows   %8.0f MW   $%.1fM booked" %
          (len(orders), orders.mw.sum(), orders.order_value.sum() / 1e6))
    print("monthly    %6d rows" % len(monthly))
    print("lookback   %6d rows" % len(lookback))
    print("exceptions %6d rows   $%.1fM material at risk" %
          (len(exceptions), exceptions.material_at_risk.sum() / 1e6))
    live = orders[~orders.cancelled]
    print()
    print("slip by REGION (months) and share of bookings:")
    s = live.groupby("region").slip_months.agg(["mean", "median",
                                                lambda x: x.quantile(0.9)])
    s.columns = ["mean", "median", "p90"]
    s["share_%"] = (orders.groupby("region").order_value.sum()
                    / orders.order_value.sum() * 100).round(1)
    print(s.round(2).to_string())
    print()
    print("slip by MARKET (months):")
    m = live.groupby("market").slip_months.agg(["mean", "median",
                                                lambda x: x.quantile(0.9)])
    m.columns = ["mean", "median", "p90"]
    m["share_%"] = (orders.groupby("market").order_value.sum()
                    / orders.order_value.sum() * 100).round(1)
    print(m.sort_values("p90").round(2).to_string())
    print()
    apac = live[live.region == "APAC"]
    india = apac[apac.market == "India"]
    rest = apac[apac.market != "India"]
    print("EDGE CASE: India inside APAC")
    print("  India        p90 slip %.1f mo, %.0f%% of APAC bookings"
          % (india.slip_months.quantile(0.9),
             orders[orders.market == "India"].order_value.sum()
             / orders[orders.region == "APAC"].order_value.sum() * 100))
    print("  rest of APAC p90 slip %.1f mo" % rest.slip_months.quantile(0.9))
    print("  APAC blended p90 slip %.1f mo  <- the number a regional average reports"
          % apac.slip_months.quantile(0.9))


if __name__ == "__main__":
    main()
