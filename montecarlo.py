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
PIPELINE_SIMS = 600          # forward bookings layer, heavier per sim
HORIZON_MONTHS = 12
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
MATERIAL_RATE = 0.50         # matches simulate.py

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

    def sample(self, orders: pd.DataFrame, rng: np.random.Generator,
               min_slip: np.ndarray | None = None) -> np.ndarray:
        """Draw a slip per order.

        `min_slip` handles orders already past due. Such an order has demonstrated
        a slip of at least the time elapsed since its expected date, so its draw
        must come from the conditional distribution slip >= elapsed. Clamping an
        unconditional draw upward instead piles every overdue order onto the first
        forecast month and makes that month look far more certain than it is.
        """
        out = np.empty(len(orders))
        keys = orders[self.group_col].to_numpy()
        for key in np.unique(keys):
            mask = keys == key
            pool = self.pools.get(key, self.fallback)
            idx = np.flatnonzero(mask)
            if min_slip is None:
                out[idx] = rng.choice(pool, size=idx.size, replace=True)
                continue
            for i in idx:
                floor = min_slip[i]
                if floor <= 0:
                    out[i] = rng.choice(pool)
                    continue
                tail = pool[pool >= floor]
                # if nothing in this market has ever run that late, fall back to the
                # whole book's tail, then to the floor itself
                if tail.size == 0:
                    tail = self.fallback[self.fallback >= floor]
                out[i] = rng.choice(tail) if tail.size else floor
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

    # Orders already past due at the cutoff have demonstrated at least that much
    # slip, so they are drawn from the conditional distribution slip >= elapsed
    # rather than clamped. Clamping put every overdue order in the first forecast
    # month and made that month the most certain-looking and least real on the page.
    cutoff_key = cutoff.to_period("M").ordinal
    elapsed = np.maximum(cutoff_key + 1 - base_exp, 0).astype(float)

    for s in range(n_sims):
        slip = model.sample(book, RNG, min_slip=elapsed)
        landing = np.maximum(base_exp + np.rint(slip).astype(int), cutoff_key + 1)
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


def run_mc_pipeline(orders: pd.DataFrame, model: EmpiricalSlipModel,
                    cutoff: pd.Timestamp, n_sims: int = N_SIMS,
                    horizon: int = HORIZON_MONTHS, lookback_months: int = 6):
    """Forward bookings not yet placed.

    A demand plan is firm backlog plus expected new business. run_mc covers the firm
    half. This covers the rest: draw a booking count per future month from recent
    history, give each new order a promised lead time and a market-conditioned slip,
    and land it. Orders booked far enough out do not convert inside the horizon at
    all, which is why this layer builds rather than depletes.
    """
    recent = orders[orders.booked_month > cutoff - pd.DateOffset(months=lookback_months)]
    by_month = recent.groupby(["booked_month", "market"]).size().reset_index(name="n")
    rate = by_month.groupby("market").n.mean().to_dict()          # orders/month/market
    mw_pool = {m: g.mw.to_numpy() for m, g in recent.groupby("market")}
    asp = {m: (g.order_value / g.mw).mean() for m, g in recent.groupby("market")}
    promised_pool = recent.promised_months.to_numpy()

    months = [cutoff + pd.DateOffset(months=i + 1) for i in range(horizon)]
    month_keys = np.array([m.to_period("M").ordinal for m in months])
    markets = list(rate)

    total = np.zeros((n_sims, horizon))
    per_scope = {}
    mk_region = orders.drop_duplicates("market").set_index("market").region.to_dict()
    for m in markets:
        per_scope.setdefault(mk_region[m], np.zeros((n_sims, horizon)))
    per_scope["India (in APAC)"] = np.zeros((n_sims, horizon))

    for s in range(n_sims):
        for j_book, book_key in enumerate(month_keys):
            for market in markets:
                n = RNG.poisson(rate[market])
                if n == 0:
                    continue
                mw = RNG.choice(mw_pool[market], size=n, replace=True)
                value = mw * asp[market] * MATERIAL_RATE
                promised = RNG.choice(promised_pool, size=n, replace=True)
                pool = model.pools.get(market, model.fallback)
                slip = RNG.choice(pool, size=n, replace=True)
                landing = book_key + promised + np.rint(slip).astype(int)
                for j, mk in enumerate(month_keys):
                    hit = landing == mk
                    if not hit.any():
                        continue
                    v = value[hit].sum()
                    total[s, j] += v
                    per_scope[mk_region[market]][s, j] += v
                    if market == "India":
                        per_scope["India (in APAC)"][s, j] += v
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
def coverage_check(orders: pd.DataFrame, cutoffs, horizon: int = 6,
                   n_sims: int = 800) -> pd.DataFrame:
    """Walk-forward. At each historical cutoff, refit the slip model on what had
    actually converted by that date, simulate forward using only orders open then,
    and compare the realised material against the predicted interval.

    The refit matters. Fitting one model on the full history and reusing it at every
    past cutoff lets the earliest forecasts draw on slips that had not happened yet,
    which flatters coverage. Holding out the conformal multiplier does not fix that,
    because the multiplier sits on top of quantiles the model produced.
    """
    rows = []
    for cutoff in cutoffs:
        book = open_backlog(orders, cutoff)
        if book.empty:
            continue
        seen = orders[(~orders.cancelled) & (orders.actual_conversion.notna())
                      & (orders.actual_conversion <= cutoff)]
        if len(seen) < 20:
            continue
        model = EmpiricalSlipModel(seen, group_col="market")
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


def conformal_split(cov: pd.DataFrame, n_cal_cutoffs: int, alpha: float = 0.20):
    """Split conformal with a genuine holdout, and signed rather than symmetric.

    Two things this fixes.

    Holdout: fitting the multiplier on the same points that then score it makes the
    reported coverage a restatement of the chosen rank. With n=48 and the 80th
    percentile rank, at least 40 of 48 land inside by construction, so "83%" is
    arithmetic, not a result. Here the earliest cutoffs calibrate and the latest
    are scored, so the reported number is out of sample.

    Signed: a symmetric p50 +/- q*spread band cannot correct a centre that is off.
    Scoring the signed residual and taking the lower and upper tails separately
    lets the correction be asymmetric, which is what a one-sided miss needs.
    """
    cov = cov.copy()
    cov["offset"] = ((cov.month.dt.year - cov.cutoff.dt.year) * 12
                     + (cov.month.dt.month - cov.cutoff.dt.month))
    cutoffs = sorted(cov.cutoff.unique())
    cal_cut = cutoffs[:n_cal_cutoffs]
    cov["split"] = np.where(cov.cutoff.isin(cal_cut), "calibration", "test")
    cal = cov[cov.split == "calibration"]

    def fit(sub):
        sp = (sub.p90 - sub.p10).replace(0, np.nan)
        s = np.sort(((sub.actual - sub.p50) / sp).dropna().to_numpy())
        n = len(s)
        if n == 0:
            return 0.0, 0.0, 0
        k_lo = max(int(np.ceil((n + 1) * (alpha / 2))), 1) - 1
        k_hi = min(int(np.ceil((n + 1) * (1 - alpha / 2))), n) - 1
        return float(s[k_lo]), float(s[k_hi]), n

    # Pooled fit, kept for comparison.
    q_lo, q_hi, n_cal = fit(cal)

    # Per-horizon fit. The residual is organised by how far ahead the forecast
    # reaches, not by geography, so one multiplier across all horizons cancels
    # opposite biases against each other instead of correcting either.
    per = {}
    for off, sub in cal.groupby("offset"):
        lo, hi, n_off = fit(sub)
        per[int(off)] = (lo, hi, n_off)

    lo_arr = cov.offset.map(lambda o: per.get(int(o), (q_lo, q_hi, 0))[0]).to_numpy()
    hi_arr = cov.offset.map(lambda o: per.get(int(o), (q_lo, q_hi, 0))[1]).to_numpy()
    sp_all = (cov.p90 - cov.p10).to_numpy()
    cov["cal80_lo"] = cov.p50.to_numpy() + lo_arr * sp_all
    cov["cal80_hi"] = cov.p50.to_numpy() + hi_arr * sp_all
    cov["in_80_cal"] = (cov.actual >= cov.cal80_lo) & (cov.actual <= cov.cal80_hi)
    # what a single pooled multiplier would have done, for the comparison
    cov["pool80_lo"] = cov.p50 + q_lo * sp_all
    cov["pool80_hi"] = cov.p50 + q_hi * sp_all
    cov["in_80_pool"] = (cov.actual >= cov.pool80_lo) & (cov.actual <= cov.pool80_hi)

    test = cov[cov.split == "test"]
    miss = cov[~cov.in_80]
    stats = dict(
        n_cal=n_cal, n_test=len(test),
        q_lo=q_lo, q_hi=q_hi,
        raw_cov_test=float(test.in_80.mean() * 100),
        cal_cov_test=float(test.in_80_cal.mean() * 100),
        pool_cov_test=float(test.in_80_pool.mean() * 100),
        raw_cov_all=float(cov.in_80.mean() * 100),
        # the raw band is p90-p10, i.e. 1.0 spreads. Dividing by the nominal
        # coverage instead of the band width overstated this by 25%.
        width_ratio=float(q_hi - q_lo),
        centre_shift=float((q_hi + q_lo) / 2),
        miss_low_offsets=sorted({int(o) for o in miss[miss.actual < miss.p10].offset}),
        miss_high_offsets=sorted({int(o) for o in miss[miss.actual > miss.p90].offset}),
        per_horizon={k: (round(v[0], 3), round(v[1], 3), v[2]) for k, v in per.items()},
    )
    return cov, stats


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

    # firm: material against orders already on the books
    months, firm, firm_scope = run_mc(book, model, cutoff)
    # pipeline: material against bookings not yet placed
    _, pipe, pipe_scope = run_mc_pipeline(orders, model, cutoff, n_sims=PIPELINE_SIMS)

    # combine. Draw counts differ, so pair each firm draw with a random pipeline draw.
    idx = RNG.integers(0, pipe.shape[0], size=firm.shape[0])
    total = firm + pipe[idx]
    all_scope = {}
    for k in set(firm_scope) | set(pipe_scope):
        f = firm_scope.get(k, np.zeros_like(firm))
        p = pipe_scope.get(k, np.zeros((pipe.shape[0], firm.shape[1])))
        all_scope[k] = f + p[idx]

    frames = []
    for lbl, draws in (("firm", firm), ("pipeline", pipe[idx]), ("all", total)):
        d = quantile_frame(months, draws, "TOTAL")
        d["layer"] = lbl
        frames.append(d)
    for r, draws in all_scope.items():
        d = quantile_frame(months, draws, r)
        d["layer"] = "all"
        frames.append(d)
    mc = pd.concat(frames, ignore_index=True)

    tot = mc[(mc.scope == "TOTAL") & (mc.layer == "all")].reset_index(drop=True)
    fm = mc[(mc.scope == "TOTAL") & (mc.layer == "firm")].reset_index(drop=True)
    pl = mc[(mc.scope == "TOTAL") & (mc.layer == "pipeline")].reset_index(drop=True)

    print("Layer 2  forward material requirement, $M, %d months from %s" %
          (HORIZON_MONTHS, cutoff.date()))
    show = pd.DataFrame({
        "month": tot.month,
        "firm": (fm.p50 / 1e6).round(1),
        "pipeline": (pl.p50 / 1e6).round(1),
        "p10": (tot.p10 / 1e6).round(1),
        "p50": (tot.p50 / 1e6).round(1),
        "p90": (tot.p90 / 1e6).round(1),
    })
    show["spread"] = (show.p90 - show.p10).round(1)
    show["firm_%"] = (fm.p50 / tot.p50 * 100).round(0)
    print(show.to_string(index=False))
    print()

    # quantiles do not add: compare sum of per-region p90 vs portfolio p90.
    # India is excluded from the sum because it is nested inside APAC and would
    # otherwise be counted twice.
    naive = 0.0
    for r, draws in all_scope.items():
        if r.startswith("India"):
            continue
        naive += np.quantile(draws, 0.90, axis=0)
    portfolio = np.quantile(total, 0.90, axis=0)
    overstatement = (naive.sum() / portfolio.sum() - 1) * 100
    print("Sum of per-region P90 vs portfolio P90: %.1f%% overstatement" % overstatement)
    print("  (this magnitude is a readout of the independence assumption in the slip")
    print("   model: orders are drawn i.i.d., with no shared shock)")
    print()

    cutoffs = [orders.booked_month.max() - pd.DateOffset(months=k)
               for k in (14, 13, 12, 11, 10, 9, 8, 7)]
    cov = coverage_check(orders, cutoffs)

    stats = {}
    if not cov.empty:
        cov, stats = conformal_split(cov, n_cal_cutoffs=5)
        miss = cov[~cov.in_80]
        stats["miss_high"] = int((miss.actual > miss.p90).sum())
        stats["miss_low"] = int((miss.actual < miss.p10).sum())
        stats["overstatement"] = float(overstatement)

        print("Layer 3  calibration, walk-forward from %d cutoffs" % len(cutoffs))
        print("  calibrated on the first 5 cutoffs (n=%d), scored on the last 3 (n=%d)"
              % (stats["n_cal"], stats["n_test"]))
        print("  raw 80%% interval, all cutoffs:              %.0f%%" % stats["raw_cov_all"])
        print("  of %d misses, %d were above the high bound, %d below the low"
              % (len(miss), stats["miss_high"], stats["miss_low"]))
        sided = abs(stats["miss_high"] - stats["miss_low"]) / max(len(miss), 1)
        print("  -> %s" % ("error is one-sided; the signed correction shifts the centre"
                           if sided > 0.5 else
                           "error is roughly two-sided; the signed correction is close to symmetric"))
        print("  lower / upper conformal scores:             %+.3f / %+.3f"
              % (stats["q_lo"], stats["q_hi"]))
        print("  implied centre shift:                       %+.3f spreads"
              % stats["centre_shift"])
        print("  band width vs raw:                          %.2fx" % stats["width_ratio"])
        print("  HELD OUT raw coverage:                      %.0f%%" % stats["raw_cov_test"])
        print("  HELD OUT calibrated coverage:               %.0f%%" % stats["cal_cov_test"])

        # apply the calibration to the combined forward view
        sp = (tot.p90 - tot.p10).to_numpy()
        cal_lo = tot.p50.to_numpy() + stats["q_lo"] * sp
        cal_hi = tot.p50.to_numpy() + stats["q_hi"] * sp
        sel = (mc.scope == "TOTAL") & (mc.layer == "all")
        mc.loc[sel, "cal80_lo"] = cal_lo
        mc.loc[sel, "cal80_hi"] = cal_hi

    cov.to_csv(os.path.join(DATA, "mc_coverage.csv"), index=False)
    mc.to_csv(os.path.join(DATA, "mc_monthly.csv"), index=False)
    if stats:
        print()
        print("  forward view, first 3 months, raw vs calibrated 80%% ($M):")
        for j in range(3):
            print("    %s   raw %5.1f - %5.1f     calibrated %5.1f - %5.1f"
                  % (tot.month.iloc[j].date(),
                     tot.p10.iloc[j] / 1e6, tot.p90.iloc[j] / 1e6,
                     cal_lo[j] / 1e6, cal_hi[j] / 1e6))
        pd.Series(stats).to_csv(os.path.join(DATA, "mc_stats.csv"), header=False)

    with open(os.path.join(DATA, "mc_summary.txt"), "w") as f:
        f.write("SYNTHETIC DATA - demonstrates method only\n\n")
        f.write("cutoff: %s\nopen orders: %d\nopen material: $%.1fM\n\n"
                % (cutoff.date(), len(book), book.material_committed.sum() / 1e6))
        f.write("next 12 months, material required ($M)\n")
        f.write(show.to_string(index=False))
        f.write("\n\nsum of per-region P90 overstates portfolio P90 by %.1f%%\n" % overstatement)
        if stats:
            f.write("held-out raw coverage: %.0f%%\n" % stats["raw_cov_test"])
            f.write("held-out calibrated coverage: %.0f%%\n" % stats["cal_cov_test"])
            f.write("conformal q_lo/q_hi: %+.4f / %+.4f\n" % (stats["q_lo"], stats["q_hi"]))
            f.write("band width vs raw: %.3f\n" % stats["width_ratio"])
    print("\nwrote mc_monthly.csv, mc_coverage.csv, mc_summary.txt")


if __name__ == "__main__":
    main()
