"""
Forward Monte Carlo over open backlog.

SYNTHETIC DATA. Demonstrates a method, not Nextpower's business.

Takes the open order book at a cutoff date and resamples each order's remaining
schedule slip many times, rolling the portfolio up by month. Produces a distribution
of material required per month rather than a point number.

Three layers, deliberately separated so the swappable one is obvious:

  1. SLIP MODEL      where each order's slip distribution comes from.
                     Here: empirical resampling from realised history, by region.
                     In production: quantile regression conditioned on region,
                     pipeline stage, interconnection status, developer, size.
                     Swap `EmpiricalSlipModel` for a fitted model, nothing else changes.

  2. AGGREGATION     Monte Carlo. Needed because quantiles do not add: the P90 of a
                     sum is not the sum of P90s, and summing per-order worst cases
                     overstates portfolio exposure badly.

  3. CALIBRATION     conformal-style coverage check. Ignores what the model claims
                     about its own uncertainty and measures what actually landed
                     inside the stated interval out of sample.

Outputs to ./data:
    mc_monthly.csv    per-month material requirement quantiles, total and by region
    mc_coverage.csv   calibration: stated interval vs realised coverage
    mc_summary.txt    headline numbers
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd

RNG = np.random.default_rng(11)

N_SIMS = 4000
HORIZON_MONTHS = 12
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


# ----------------------------------------------------------------------------
# Layer 1: slip model. This is the piece you replace with a fitted model.
# ----------------------------------------------------------------------------
class EmpiricalSlipModel:
    """Resamples observed slip, conditioned on region.

    A fitted replacement would expose the same `sample(orders, rng)` interface and
    condition on more than region. Everything downstream is indifferent to which
    is used, which is the point of keeping this behind a class.
    """

    def __init__(self, history: pd.DataFrame, group_col: str = "market"):
        self.group_col = group_col
        self.pools = {
            key: grp["slip_months"].to_numpy()
            for key, grp in history.groupby(group_col)
        }
        self.fallback = history["slip_months"].to_numpy()

    def conditional_quantiles(self, q=(0.1, 0.5, 0.9)) -> pd.DataFrame:
        rows = []
        for key, pool in self.pools.items():
            row = {self.group_col: key, "n": len(pool)}
            for qq in q:
                row[f"p{int(qq * 100)}"] = float(np.quantile(pool, qq))
            rows.append(row)
        return pd.DataFrame(rows).sort_values("p90")

    def sample(self, orders: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
        out = np.empty(len(orders))
        keys = orders[self.group_col].to_numpy()
        for key in np.unique(keys):
            mask = keys == key
            pool = self.pools.get(key, self.fallback)
            out[mask] = rng.choice(pool, size=mask.sum(), replace=True)
        return out


# ----------------------------------------------------------------------------
# Layer 2: Monte Carlo aggregation
# ----------------------------------------------------------------------------
def open_backlog(orders: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    return orders[
        (orders.booked_month <= cutoff)
        & (~orders.cancelled)
        & ((orders.actual_conversion.isna()) | (orders.actual_conversion > cutoff))
    ].copy()


def run_mc(book: pd.DataFrame, model: EmpiricalSlipModel, cutoff: pd.Timestamp,
           n_sims: int = N_SIMS, horizon: int = HORIZON_MONTHS):
    """Returns (total_draws, region_draws).

    total_draws:  n_sims x horizon, material $ by month offset from cutoff
    region_draws: dict region -> n_sims x horizon
    """
    months = [cutoff + pd.DateOffset(months=i + 1) for i in range(horizon)]
    month_keys = np.array([m.to_period("M").ordinal for m in months])

    base_exp = book["expected_conversion"].dt.to_period("M").apply(lambda p: p.ordinal).to_numpy()
    material = book["material_committed"].to_numpy()
    regions = book["region"].to_numpy()
    markets = book["market"].to_numpy()

    # top-level regions, plus India tracked on its own because its behaviour is an
    # outlier that the APAC average hides
    scopes = {r: regions == r for r in np.unique(regions)}
    if (markets == "India").any():
        scopes["India (in APAC)"] = markets == "India"

    total = np.zeros((n_sims, horizon))
    per_scope = {k: np.zeros((n_sims, horizon)) for k in scopes}

    for s in range(n_sims):
        slip = model.sample(book, RNG)
        landing = base_exp + np.rint(slip).astype(int)
        for j, mk in enumerate(month_keys):
            hit = landing == mk
            if not hit.any():
                continue
            total[s, j] = material[hit].sum()
            for k, sel in scopes.items():
                rm = hit & sel
                if rm.any():
                    per_scope[k][s, j] = material[rm].sum()
    return months, total, per_scope


def quantile_frame(months, draws, label) -> pd.DataFrame:
    rows = []
    for j, m in enumerate(months):
        col = draws[:, j]
        row = {"scope": label, "month": m, "mean": col.mean()}
        for q in QUANTILES:
            row[f"p{int(q * 100)}"] = float(np.quantile(col, q))
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Layer 3: calibration. Does a stated interval actually cover?
# ----------------------------------------------------------------------------
def coverage_check(orders: pd.DataFrame, model: EmpiricalSlipModel,
                   cutoffs, horizon: int = 6, n_sims: int = 800) -> pd.DataFrame:
    """Walk-forward. At each historical cutoff, simulate forward using only orders
    open at that time, then compare the realised material against the predicted
    interval. Reports how often the truth fell inside, against how often it should.
    """
    rows = []
    for cutoff in cutoffs:
        book = open_backlog(orders, cutoff)
        if book.empty:
            continue
        months, total, _ = run_mc(book, model, cutoff, n_sims=n_sims, horizon=horizon)
        for j, m in enumerate(months):
            actual = orders[
                (orders.actual_conversion == m) & (~orders.cancelled)
                & (orders.booked_month <= cutoff)
            ]["material_committed"].sum()
            col = total[:, j]
            rows.append(dict(
                cutoff=cutoff, month=m, actual=actual,
                p10=np.quantile(col, 0.10), p50=np.quantile(col, 0.50),
                p90=np.quantile(col, 0.90),
                p25=np.quantile(col, 0.25), p75=np.quantile(col, 0.75),
            ))
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["in_80"] = (df.actual >= df.p10) & (df.actual <= df.p90)
    df["in_50"] = (df.actual >= df.p25) & (df.actual <= df.p75)
    return df


def main() -> None:
    orders = pd.read_csv(os.path.join(DATA, "orders.csv"),
                         parse_dates=["booked_month", "expected_conversion",
                                      "actual_conversion"])
    history = orders[~orders.cancelled]
    model = EmpiricalSlipModel(history, group_col="market")

    print("Layer 1  conditional slip quantiles (months), resampled from history")
    print("         conditioned on MARKET, not region, because the regional average")
    print("         hides India inside APAC and MENA inside EMEA")
    print(model.conditional_quantiles().to_string(index=False))
    print()
    reg = EmpiricalSlipModel(history, group_col="region").conditional_quantiles()
    print("         for contrast, the same thing at region level:")
    print(reg.to_string(index=False))
    print()

    cutoff = orders.booked_month.max()
    book = open_backlog(orders, cutoff)
    months, total, per_region = run_mc(book, model, cutoff)

    frames = [quantile_frame(months, total, "TOTAL")]
    for r, draws in per_region.items():
        frames.append(quantile_frame(months, draws, r))
    mc = pd.concat(frames, ignore_index=True)
    mc.to_csv(os.path.join(DATA, "mc_monthly.csv"), index=False)

    tot = mc[mc.scope == "TOTAL"]
    print("Layer 2  forward material requirement, $M, %d sims over %d months from %s"
          % (N_SIMS, HORIZON_MONTHS, cutoff.date()))
    show = tot[["month", "p10", "p50", "p90"]].copy()
    for c in ("p10", "p50", "p90"):
        show[c] = (show[c] / 1e6).round(1)
    show["spread"] = (show.p90 - show.p10).round(1)
    print(show.to_string(index=False))
    print()

    # quantiles do not add: compare sum of per-region p90 vs portfolio p90.
    # India is excluded from the sum because it is nested inside APAC and would
    # otherwise be counted twice.
    naive = 0.0
    for r, draws in per_region.items():
        if r.startswith("India"):
            continue
        naive += np.quantile(draws, 0.90, axis=0)
    portfolio = np.quantile(total, 0.90, axis=0)
    overstatement = (naive.sum() / portfolio.sum() - 1) * 100
    print("Sum of per-region P90 vs portfolio P90: %.1f%% overstatement"
          % overstatement)
    print()

    cutoffs = [orders.booked_month.max() - pd.DateOffset(months=k)
               for k in (14, 13, 12, 11, 10, 9, 8, 7)]
    cov = coverage_check(orders, model, cutoffs)

    # conformal correction: measure how wrong the stated interval actually was,
    # then widen it by the factor that would have achieved nominal coverage.
    # Split-conformal on a spread-scaled residual.
    factor_80 = factor_50 = 1.0
    if not cov.empty:
        spread = (cov.p90 - cov.p10).replace(0, np.nan)
        score = (cov.actual - cov.p50).abs() / spread
        score = score.dropna()
        if len(score):
            n = len(score)
            k80 = min(int(np.ceil((n + 1) * 0.80)), n) - 1
            k50 = min(int(np.ceil((n + 1) * 0.50)), n) - 1
            q80 = float(np.sort(score.to_numpy())[k80])
            q50 = float(np.sort(score.to_numpy())[k50])
            # raw interval half-width is (p90-p10)/2, i.e. score of 0.5
            factor_80 = q80 / 0.5
            factor_50 = q50 / 0.25
            cov["cal80_lo"] = cov.p50 - q80 * spread
            cov["cal80_hi"] = cov.p50 + q80 * spread
            cov["in_80_cal"] = (cov.actual >= cov.cal80_lo) & (cov.actual <= cov.cal80_hi)

    cov.to_csv(os.path.join(DATA, "mc_coverage.csv"), index=False)
    if not cov.empty:
        print("Layer 3  calibration, walk-forward from %d cutoffs (n=%d month-forecasts)"
              % (len(cutoffs), len(cov)))
        print("  raw 80%% interval realised coverage:        %.0f%%" % (cov.in_80.mean() * 100))
        print("  raw 50%% interval realised coverage:        %.0f%%" % (cov.in_50.mean() * 100))
        print("  conformal widening needed for 80%%:         %.2fx" % factor_80)
        if "in_80_cal" in cov:
            print("  calibrated 80%% interval coverage:          %.0f%%"
                  % (cov.in_80_cal.mean() * 100))

        # apply the calibration to the forward view
        tot_spread = (tot.p90 - tot.p10).to_numpy()
        cal_lo = tot.p50.to_numpy() - (factor_80 * 0.5) * tot_spread
        cal_hi = tot.p50.to_numpy() + (factor_80 * 0.5) * tot_spread
        mc.loc[mc.scope == "TOTAL", "cal80_lo"] = cal_lo
        mc.loc[mc.scope == "TOTAL", "cal80_hi"] = cal_hi
        mc.to_csv(os.path.join(DATA, "mc_monthly.csv"), index=False)
        print()
        print("  forward view, first 3 months, raw vs calibrated 80%% ($M):")
        for j in range(3):
            print("    %s   raw %5.1f - %5.1f     calibrated %5.1f - %5.1f"
                  % (tot.month.iloc[j].date(),
                     tot.p10.iloc[j] / 1e6, tot.p90.iloc[j] / 1e6,
                     cal_lo[j] / 1e6, cal_hi[j] / 1e6))

    with open(os.path.join(DATA, "mc_summary.txt"), "w") as f:
        f.write("SYNTHETIC DATA - demonstrates method only\n\n")
        f.write("cutoff: %s\nopen orders: %d\nopen material: $%.1fM\n\n"
                % (cutoff.date(), len(book), book.material_committed.sum() / 1e6))
        f.write("next 12 months, material required ($M)\n")
        f.write(show.to_string(index=False))
        f.write("\n\nsum of per-region P90 overstates portfolio P90 by %.1f%%\n" % overstatement)
        if not cov.empty:
            f.write("stated 80%% interval realised coverage: %.0f%%\n" % (cov.in_80.mean() * 100))
    print("\nwrote mc_monthly.csv, mc_coverage.csv, mc_summary.txt")


if __name__ == "__main__":
    main()
