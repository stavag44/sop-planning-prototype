"""
Builds a self-contained HTML S&OP dashboard from the simulated data.

SYNTHETIC DATA. Demonstrates a method, not Nextpower's business.

Five things a spreadsheet and a deck structurally cannot do:
  1. material requirement as a calibrated distribution, not a point
  2. Monte Carlo portfolio aggregation, including the quantiles-do-not-add effect
  3. realization rate as an on-page control rather than three static slides
  4. a conversion lookback that accumulates bias by region
  5. exceptions at order level, not regional totals
"""

from __future__ import annotations

import os
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "index.html")

# Palette taken from nextpower.com's own CSS custom properties, read off the live
# site: --primary #fe5000, --secondary #111827, --tertiary #3b82f6,
# --success #22c55e, --warning #eab308, --surface #f5f5f5, --input-border #d4d4d4.
# Their --chart-1 through --chart-5 map to primary/secondary/tertiary/success/warning,
# so the series colours below are the company's own chart palette.
ORANGE = "#fe5000"          # --primary
DARK = "#111827"            # --secondary
INK = "#111111"             # --black
SURFACE = "#f5f5f5"         # --surface
BORDER = "#d4d4d4"          # --input-border
MUTED = "#737373"           # --icon-muted
SUBTLE = "#525252"          # --text-subtle

NAVY = DARK                 # headings
ACCENT = ORANGE
BAND = "rgba(254,80,0,0.14)"
BAND2 = "rgba(17,24,39,0.16)"

REGION_COLORS = {
    "North America": DARK,
    "EMEA": "#3b82f6",
    "APAC": ORANGE,
    "LATAM": "#22c55e",
}

MARKET_COLORS = {
    "US / Canada": DARK,
    "Europe": "#3b82f6",
    "MENA": "#eab308",
    "Australia/SEA": "#64748b",
    "India": ORANGE,
    "LATAM": "#22c55e",
}

MARKET_REGION = {
    "US / Canada": "North America",
    "Europe": "EMEA",
    "MENA": "EMEA",
    "Australia/SEA": "APAC",
    "India": "APAC",
    "LATAM": "LATAM",
}

PLOT_BG = "#ffffff"
# nextpower.com sets "PP Neue Montreal" with this fallback stack. The face itself is
# a licensed commercial typeface and is not loaded here; the fallback stack is.
FONT = ('"PP Neue Montreal", system-ui, -apple-system, BlinkMacSystemFont, '
        '"Segoe UI", Roboto, "Noto Sans", Ubuntu, Cantarell, "Helvetica Neue", sans-serif')


def V(a):
    """Plotly does not serialise pandas 3.x Series or extension arrays; they arrive
    in the page as empty traces with no error. Hand it plain values."""
    if isinstance(a, (pd.Series, pd.Index)):
        a = a.to_numpy()
    if isinstance(a, np.ndarray):
        if np.issubdtype(a.dtype, np.datetime64):
            return a
        if a.dtype == object:
            return a.tolist()
        return np.asarray(a, dtype=float)
    if isinstance(a, (list, tuple)):
        return list(a)
    return a


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def base_layout(fig, height=380, legend=True):
    fig.update_layout(
        height=height,
        margin=dict(l=56, r=24, t=16, b=44),
        plot_bgcolor=PLOT_BG,
        paper_bgcolor=PLOT_BG,
        font=dict(family=FONT, size=12, color=INK),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    bgcolor="rgba(0,0,0,0)", font=dict(size=11)),
        hovermode="x unified",
    )
    fig.update_xaxes(showgrid=False, linecolor=BORDER, ticks="outside",
                     tickcolor=BORDER)
    fig.update_yaxes(gridcolor="#ededed", zeroline=False, linecolor=BORDER)
    return fig


# ---------------------------------------------------------------- 1 + 2
def fig_forward(mc: pd.DataFrame) -> go.Figure:
    # reset_index is load-bearing: these are three differently-indexed slices, and
    # adding two of them index-aligned yields NaN and a silently missing fill.
    sel = (mc.scope == "TOTAL")
    t = mc[sel & (mc.layer == "all")].sort_values("month").reset_index(drop=True)
    firm = mc[sel & (mc.layer == "firm")].sort_values("month").reset_index(drop=True)
    pipe = mc[sel & (mc.layer == "pipeline")].sort_values("month").reset_index(drop=True)
    x = t.month
    firm_y = firm.p50.to_numpy() / 1e6
    stack_y = (firm.p50.to_numpy() + pipe.p50.to_numpy()) / 1e6
    fig = go.Figure()

    # composition of the expected total: firm backlog, then bookings not yet placed
    fig.add_trace(go.Scatter(
        x=V(x), y=V(firm_y), mode="lines", line=dict(width=0),
        fill="tozeroy", fillcolor=hex_to_rgba(DARK, 0.55),
        name="already booked",
        hovertemplate="already booked: %{y:.1f}M<extra></extra>"))
    fig.add_trace(go.Scatter(
        x=V(x), y=V(stack_y), mode="lines", line=dict(width=0),
        fill="tonexty", fillcolor=hex_to_rgba(ORANGE, 0.55),
        name="not yet booked",
        hovertemplate="incl. not yet booked: %{y:.1f}M<extra></extra>"))

    # Uncertainty as bounding lines rather than another translucent fill. Two
    # stacked areas plus two shaded bands turns the whole plot to mud and buries
    # the composition, which is the thing worth seeing here.
    lo = t.cal80_lo if ("cal80_lo" in t and t.cal80_lo.notna().any()) else t.p10
    hi = t.cal80_hi if ("cal80_hi" in t and t.cal80_hi.notna().any()) else t.p90
    fig.add_trace(go.Scatter(x=V(x), y=V(hi / 1e6), mode="lines",
                             line=dict(color=INK, width=1.2, dash="dot"),
                             name="80% interval",
                             hovertemplate="high: %{y:.1f}M<extra></extra>"))
    fig.add_trace(go.Scatter(x=V(x), y=V(lo / 1e6), mode="lines",
                             line=dict(color=INK, width=1.2, dash="dot"),
                             name="80% interval", showlegend=False,
                             hovertemplate="low: %{y:.1f}M<extra></extra>"))
    fig.add_trace(go.Scatter(x=V(x), y=V(t.p50 / 1e6), mode="lines+markers",
                             line=dict(color=INK, width=2.5),
                             marker=dict(size=5), name="expected total",
                             hovertemplate="expected total: %{y:.1f}M<extra></extra>"))
    fig.update_yaxes(title_text="$M of material", rangemode="tozero")
    return base_layout(fig, 420)


def fig_region_bands(mc: pd.DataFrame) -> go.Figure:
    """Small multiples. North America is roughly 69% of volume, so on one shared
    axis it flattens the other three into unreadable lines at the bottom. Each
    panel gets its own scale; the cost is that magnitudes are not comparable by
    eye across panels, which is why the axis maximum is printed on each."""
    order = ["North America", "EMEA", "APAC", "LATAM"]
    fig = make_subplots(rows=2, cols=2, subplot_titles=order,
                        horizontal_spacing=0.09, vertical_spacing=0.16)
    for i, region in enumerate(order):
        rr, cc = i // 2 + 1, i % 2 + 1
        r = mc[(mc.scope == region) & (mc.layer == "all")].sort_values("month")
        if r.empty:
            continue
        color = REGION_COLORS[region]
        fig.add_trace(go.Scatter(
            x=list(V(r.month)) + list(V(r.month))[::-1],
            y=list(V(r.p90 / 1e6)) + list(V(r.p10 / 1e6))[::-1],
            fill="toself", fillcolor=hex_to_rgba(color, 0.15),
            line=dict(width=0), showlegend=False, hoverinfo="skip"),
            row=rr, col=cc)
        fig.add_trace(go.Scatter(
            x=V(r.month), y=V(r.p50 / 1e6), mode="lines",
            line=dict(color=color, width=2.2), showlegend=False,
            hovertemplate=region + ": %{y:.1f}M<extra></extra>"), row=rr, col=cc)
        if region == "APAC":
            ind = mc[(mc.scope == "India (in APAC)") &
                     (mc.layer == "all")].sort_values("month")
            if not ind.empty:
                fig.add_trace(go.Scatter(
                    x=V(ind.month), y=V(ind.p50 / 1e6), mode="lines",
                    line=dict(color=INK, width=1.6, dash="dot"),
                    showlegend=False,
                    hovertemplate="India: %{y:.1f}M<extra></extra>"), row=rr, col=cc)
    # Print each panel's ceiling. Four independent scales with nothing naming them
    # invites reading a small region as comparable in size to a large one.
    for i, region in enumerate(order):
        rr, cc = i // 2 + 1, i % 2 + 1
        r = mc[(mc.scope == region) & (mc.layer == "all")]
        if r.empty:
            continue
        top = float(r.p90.max()) / 1e6
        fig.add_annotation(text="scale to $%.0fM" % top, showarrow=False,
                           xref=f"x{'' if i == 0 else i + 1} domain",
                           yref=f"y{'' if i == 0 else i + 1} domain",
                           x=0.97, y=0.97, xanchor="right", yanchor="top",
                           font=dict(size=9.5, color=MUTED))
    fig.update_yaxes(title_text="$M", rangemode="tozero", gridcolor="#ededed",
                     zeroline=False, linecolor=BORDER)
    fig.update_xaxes(showgrid=False, linecolor=BORDER, ticks="outside",
                     tickcolor=BORDER, dtick="M3", tickformat="%b<br>%Y")
    fig.update_layout(
        height=520, margin=dict(l=56, r=24, t=34, b=40),
        plot_bgcolor=PLOT_BG, paper_bgcolor=PLOT_BG,
        font=dict(family=FONT, size=11, color=INK),
        showlegend=False, hovermode="closest")
    # Only restyle the subplot titles. The axis-ceiling notes are annotations too,
    # and restyling them turned each one into a second title on its panel.
    for a in fig.layout.annotations:
        if a.text in order:
            a.font.size = 12
            a.font.color = INK
            a.x = a.x - 0.03
            a.xanchor = "left"
    return fig


def fig_slip(orders: pd.DataFrame) -> go.Figure:
    """Slip by market, ordered by tail. India is the flagged outlier."""
    live = orders[~orders.cancelled]
    order = (live.groupby("market").slip_months.quantile(0.9)
             .sort_values().index.tolist())
    fig = go.Figure()
    for market in order:
        s = live[live.market == market].slip_months
        fig.add_trace(go.Box(
            y=V(s), name=market, marker_color=MARKET_COLORS.get(market, MUTED),
            # Show the outliers. With boxpoints off, the whiskers are 1.5x IQR, so
            # the long tails the caption cites appear nowhere on the chart.
            boxpoints="outliers", marker=dict(size=3, opacity=0.45),
            line=dict(width=1.6),
            hoverinfo="y"))
    fig.update_yaxes(title_text="schedule slip, months")
    return base_layout(fig, 360, legend=False)


def fig_blend(orders: pd.DataFrame) -> go.Figure:
    """The regional average describes neither half. Market P90s as bars, the region
    blend as a marker sitting between them."""
    live = orders[~orders.cancelled]
    mk = live.groupby("market").slip_months.quantile(0.9)
    rg = live.groupby("region").slip_months.quantile(0.9)

    rows = []
    for region in ["North America", "LATAM", "EMEA", "APAC"]:
        markets = [m for m, r in MARKET_REGION.items() if r == region and m in mk.index]
        for m in sorted(markets, key=lambda x: mk[x]):
            rows.append((region, m, mk[m]))
    labels = [f"{m}" for _, m, _ in rows]
    vals = [v for _, _, v in rows]
    colors = [MARKET_COLORS.get(m, MUTED) for _, m, _ in rows]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=V(vals), y=labels, orientation="h", marker_color=colors,
                         text=[f"{v:.1f}" for v in vals], textposition="outside",
                         hovertemplate="%{y}: P90 %{x:.1f} months<extra></extra>",
                         name="market"))
    # One legend entry for the marker type, not per region. Naming it after a single
    # region labelled the other region's diamonds with the wrong region.
    first = True
    for region in ["EMEA", "APAC"]:
        members = [m for r, m, _ in rows if r == region]
        if not members:
            continue
        fig.add_trace(go.Scatter(
            x=V([float(rg[region])] * len(members)), y=members, mode="markers",
            marker=dict(symbol="diamond", size=11, color="#22262c",
                        line=dict(color="#fff", width=1.5)),
            name="blended regional P90", legendgroup="blend", showlegend=first,
            hovertemplate=region + " blended: P90 %{x:.1f} months<extra></extra>"))
        first = False
    fig.update_xaxes(title_text="P90 schedule slip, months")
    fig = base_layout(fig, 340)
    fig.update_layout(margin=dict(l=130, r=44, t=30, b=44), hovermode="closest")
    return fig


# ---------------------------------------------------------------- 3
def fig_scenario(monthly: pd.DataFrame) -> go.Figure:
    """Realization rate as an on-page control. Traces are pre-computed per rate and
    toggled by a slider, so the file stays self-contained with no server."""
    latest = monthly.month.max()
    snap = monthly[monthly.month == latest].set_index("region")
    regions = [r for r in REGION_COLORS if r in snap.index]
    base = np.array([snap.loc[r, "committed_material"] for r in regions]) / 1e6

    rates = [round(x, 2) for x in np.arange(0.55, 1.001, 0.05)]
    fig = go.Figure()
    for i, rate in enumerate(rates):
        fig.add_trace(go.Bar(
            x=regions, y=V(base * rate), visible=(i == len(rates) - 1),
            marker_color=[REGION_COLORS[r] for r in regions],
            hovertemplate="%{x}: $%{y:.1f}M<extra></extra>",
            text=[f"${v:.0f}M" for v in base * rate], textposition="outside",
        ))
    steps = []
    for i, rate in enumerate(rates):
        vis = [j == i for j in range(len(rates))]
        steps.append(dict(method="update", label=f"{int(rate * 100)}%",
                          args=[{"visible": vis}]))
    fig.update_layout(sliders=[dict(active=len(rates) - 1, pad=dict(t=48),
                                    currentvalue=dict(prefix="backlog realization: ",
                                                      font=dict(size=13, color=NAVY)),
                                    steps=steps)])
    # Fix the axis across all scenarios. Without this Plotly autoranges to whichever
    # rate is selected, so the bars occupy the same height at 55% as at 100% and the
    # slider appears to do nothing but relabel the axis.
    fig.update_yaxes(title_text="$M of material committed",
                     range=[0, float(base.max()) * 1.12])
    fig = base_layout(fig, 430, legend=False)
    fig.update_layout(margin=dict(l=56, r=24, t=16, b=90))
    return fig


# ---------------------------------------------------------------- 4
def fig_lookback(lookback: pd.DataFrame) -> go.Figure:
    lb = lookback.copy()
    lb = lb[lb.expected_mw > 0]
    lb["cum_bias"] = lb.groupby("region").bias_mw.cumsum()
    fig = make_subplots(rows=1, cols=2, shared_xaxes=False,
                        subplot_titles=("realization rate by region",
                                        "cumulative bias, MW"),
                        horizontal_spacing=0.09)
    for region, color in REGION_COLORS.items():
        r = lb[lb.region == region].sort_values("month")
        if r.empty:
            continue
        roll = r.set_index("month").realization.rolling(3, min_periods=1).mean()
        fig.add_trace(go.Scatter(x=V(roll.index), y=V(roll.to_numpy() * 100), mode="lines",
                                 line=dict(color=color, width=2), name=region,
                                 legendgroup=region,
                                 hovertemplate=region + ": %{y:.0f}%<extra></extra>"),
                      row=1, col=1)
        fig.add_trace(go.Scatter(x=V(r.month), y=V(r.cum_bias), mode="lines",
                                 line=dict(color=color, width=2), name=region,
                                 legendgroup=region, showlegend=False,
                                 hovertemplate=region + ": %{y:,.0f} MW<extra></extra>"),
                      row=1, col=2)
    fig.add_hline(y=100, line=dict(color=MUTED, width=1, dash="dot"), row=1, col=1)
    fig.add_hline(y=0, line=dict(color=MUTED, width=1, dash="dot"), row=1, col=2)
    fig.update_yaxes(title_text="%, 3-month mean", row=1, col=1)
    fig.update_yaxes(title_text="MW", row=1, col=2)
    fig = base_layout(fig, 400)
    fig.update_layout(hovermode="closest")
    for a in fig.layout.annotations:
        a.font.size = 12
        a.font.color = NAVY
    return fig


def fig_coverage(cov: pd.DataFrame) -> go.Figure:
    raw = cov.in_80.mean() * 100
    cal = cov.in_80_cal.mean() * 100 if "in_80_cal" in cov else np.nan
    fig = go.Figure()
    fig.add_trace(go.Bar(x=["stated", "raw, held out", "calibrated, held out"],
                         y=[80, raw, cal],
                         marker_color=[MUTED, ACCENT, NAVY],
                         text=[f"{v:.0f}%" for v in [80, raw, cal]],
                         textposition="outside",
                         hovertemplate="%{x}: %{y:.0f}%<extra></extra>"))
    fig.update_yaxes(title_text="% of months inside the interval", range=[0, 100])
    return base_layout(fig, 340, legend=False)


# ---------------------------------------------------------------- 5
def exceptions_table(exc: pd.DataFrame, n=14) -> str:
    e = exc.head(n).copy()
    e["expected"] = pd.to_datetime(e.expected_conversion).dt.strftime("%b %Y")
    e["actual"] = pd.to_datetime(e.actual_conversion).dt.strftime("%b %Y").fillna("cancelled")
    rows = []
    for _, r in e.iterrows():
        badge = ("badge-cancel" if r.reason == "cancelled" else
                 "badge-slip" if r.slip_months < 6 else "badge-bad")
        where = r.market if r.market == r.region else f"{r.region} / {r.market}"
        rows.append(
            f"<tr><td class='mono'>{r.order_id}</td>"
            f"<td>{where}</td>"
            f"<td class='num'>{r.mw:,.0f}</td>"
            f"<td>{r['expected']}</td><td>{r['actual']}</td>"
            f"<td class='num'>{'' if r.reason == 'cancelled' else f'{r.slip_months:.1f}'}</td>"
            f"<td><span class='badge {badge}'>{r.reason}</span></td>"
            f"<td class='num strong'>${r.material_at_risk/1e6:,.1f}M</td></tr>")
    return "\n".join(rows)


# ---------------------------------------------------------------- page
def main() -> None:
    orders = pd.read_csv(os.path.join(DATA, "orders.csv"),
                         parse_dates=["booked_month", "expected_conversion",
                                      "actual_conversion"])
    monthly = pd.read_csv(os.path.join(DATA, "monthly.csv"), parse_dates=["month"])
    lookback = pd.read_csv(os.path.join(DATA, "lookback.csv"), parse_dates=["month"])
    exc = pd.read_csv(os.path.join(DATA, "exceptions.csv"),
                      parse_dates=["booked_month", "expected_conversion",
                                   "actual_conversion"])
    mc = pd.read_csv(os.path.join(DATA, "mc_monthly.csv"), parse_dates=["month"])
    cov = pd.read_csv(os.path.join(DATA, "mc_coverage.csv"),
                      parse_dates=["cutoff", "month"])

    t = mc[(mc.scope == "TOTAL") & (mc.layer == "all")].sort_values("month")
    firm_t = mc[(mc.scope == "TOTAL") & (mc.layer == "firm")].sort_values("month")
    nxt = t.iloc[0]
    spread_pct = (nxt.p90 - nxt.p10) / nxt.p50 * 100
    firm_first = firm_t.p50.iloc[0] / t.p50.iloc[0] * 100
    firm_last = firm_t.p50.iloc[-1] / t.p50.iloc[-1] * 100
    firm_share = (firm_t.p50.to_numpy() / t.p50.to_numpy())
    fully_firm = int((firm_share >= 0.999).sum())
    spread_first = (t.p90.iloc[0] - t.p10.iloc[0]) / 1e6
    spread_last = (t.p90.iloc[-1] - t.p10.iloc[-1]) / 1e6
    raw_cov = cov.in_80.mean() * 100
    cal_cov = cov.in_80_cal.mean() * 100 if "in_80_cal" in cov else float("nan")
    stats = pd.read_csv(os.path.join(DATA, "mc_stats.csv"), header=None,
                        index_col=0).squeeze("columns").to_dict()
    widen = float(stats["width_ratio"])
    raw_cov_all = float(stats["raw_cov_all"])
    raw_cov_test = float(stats["raw_cov_test"])
    cal_cov_test = float(stats["cal_cov_test"])
    n_cal, n_test = int(stats["n_cal"]), int(stats["n_test"])
    miss_high, miss_low = int(stats["miss_high"]), int(stats["miss_low"])
    overstatement = float(stats["overstatement"])

    open_material = monthly[monthly.month == monthly.month.max()].committed_material.sum()

    # Period-matched to open_material: material on orders that are still open at the
    # cutoff and have slipped, not every exception across all 24 months. The two
    # tiles sit side by side, so a reader will read them as a ratio.
    cutoff = orders.booked_month.max()
    open_book = orders[(orders.booked_month <= cutoff) & (~orders.cancelled)
                       & ((orders.actual_conversion.isna())
                          | (orders.actual_conversion > cutoff))]
    at_risk = open_book[open_book.slip_months >= 2].material_committed.sum()

    n_months = int(cov.month.nunique())
    if cal_cov_test > raw_cov_test + 2:
        cal_verdict = (
            f"The correction is fitted on the earliest five cutoffs and scored on the three most "
            f"recent, which the fit never saw. On that held-out sample it moved coverage from "
            f"{raw_cov_test:.0f}% to {cal_cov_test:.0f}%.")
    elif cal_cov_test < raw_cov_test - 2:
        cal_verdict = (
            f"The correction is fitted on the earliest five cutoffs and scored on the three most "
            f"recent, which the fit never saw. On that held-out sample it made things worse: "
            f"coverage fell from {raw_cov_test:.0f}% to {cal_cov_test:.0f}%. The fitted period ran "
            f"wider than nominal, so the correction narrowed the band, and the later period did not "
            f"behave the same way. That is a negative result and it is reported as one.")
    else:
        cal_verdict = (
            f"The correction is fitted on the earliest five cutoffs and scored on the three most "
            f"recent, which the fit never saw. On that held-out sample coverage was unchanged at "
            f"{cal_cov_test:.0f}%, so the correction bought committed width and no accuracy.")
    firm_share_all = (firm_t.p50.to_numpy() / t.p50.to_numpy())
    months_half_firm = int((firm_share_all >= 0.5).sum())

    live = orders[~orders.cancelled]
    p90_market = live.groupby("market").slip_months.quantile(0.9)
    p90_region = live.groupby("region").slip_months.quantile(0.9)
    india_share_apac = (orders[orders.market == "India"].order_value.sum()
                        / orders[orders.region == "APAC"].order_value.sum() * 100)

    figs = {
        "forward": fig_forward(mc),
        "regions": fig_region_bands(mc),
        "slip": fig_slip(orders),
        "blend": fig_blend(orders),
        "scenario": fig_scenario(monthly),
        "lookback": fig_lookback(lookback),
        "coverage": fig_coverage(cov),
    }
    html = {k: v.to_html(full_html=False, include_plotlyjs=("cdn" if k == "forward" else False),
                         config={"displayModeBar": False, "responsive": True})
            for k, v in figs.items()}

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monthly S&amp;OP demand and material review</title>
<style>
  :root {{ --orange:{ORANGE}; --dark:{DARK}; --ink:{INK}; --subtle:{SUBTLE};
           --muted:{MUTED}; --line:{BORDER}; --surface:{SURFACE}; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--surface); color:var(--ink);
         font-family:{FONT}; line-height:1.55; -webkit-font-smoothing:antialiased; }}
  .topbar {{ background:var(--ink); color:#fff; }}
  .topbar .inner {{ max-width:1180px; margin:0 auto; padding:11px 20px;
      display:flex; gap:16px; align-items:baseline; }}
  .eyebrow {{ font-size:10.5px; text-transform:uppercase; letter-spacing:1.4px;
      font-weight:600; color:var(--orange); }}
  .topbar .who {{ font-size:12px; color:#d4d4d4; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:40px 20px 72px; }}
  header {{ margin-bottom:26px; }}
  h1 {{ font-size:40px; font-weight:400; line-height:1.1; margin:0 0 10px;
      color:var(--ink); letter-spacing:-1px; max-width:22ch; }}
  .sub {{ color:var(--subtle); font-size:15px; margin:0; max-width:70ch; }}
  .rule {{ height:4px; background:var(--orange); width:64px; margin:22px 0 0; }}
  .synthetic {{ background:#fff; border:1px solid var(--line);
      border-left:4px solid var(--orange);
      padding:13px 16px; font-size:13px; margin:26px 0 30px; color:var(--subtle); }}
  .synthetic strong {{ color:var(--ink); }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
      gap:1px; margin-bottom:32px; background:var(--line); border:1px solid var(--line); }}
  .kpi {{ background:#fff; padding:18px 20px; }}
  .kpi .v {{ font-size:30px; font-weight:400; color:var(--ink); letter-spacing:-1px; }}
  .kpi .l {{ font-size:10.5px; color:var(--muted); text-transform:uppercase;
      letter-spacing:1.1px; margin-top:5px; }}
  section {{ background:#fff; border:1px solid var(--line);
      padding:26px 28px 14px; margin-bottom:22px; }}
  h2 {{ font-size:10.5px; text-transform:uppercase; letter-spacing:1.4px;
      color:var(--orange); font-weight:600; margin:0 0 10px; }}
  .lede {{ font-size:16px; color:var(--ink); margin:0 0 18px; max-width:78ch;
      font-weight:400; }}
  .note {{ font-size:13px; color:var(--subtle); margin:8px 0 16px; max-width:86ch; }}
  .decisions {{ background:#fff; border:1px solid var(--line); padding:26px 28px 20px;
      margin-bottom:22px; }}
  .decisions table {{ margin-top:14px; }}
  .decisions th:first-child {{ width:23%; }}
  .decisions th:last-child {{ width:26%; }}
  .who-role {{ font-weight:600; color:var(--ink); }}
  .who-role span {{ display:block; font-weight:400; font-size:11.5px; color:var(--muted);
      text-transform:none; letter-spacing:0; margin-top:2px; }}
  .q {{ color:var(--ink); }}
  .decisions a {{ color:var(--orange); text-decoration:none;
      border-bottom:1px solid rgba(254,80,0,.35); }}
  .decisions a:hover {{ border-bottom-color:var(--orange); }}
  section {{ scroll-margin-top:16px; }}
  .closing {{ background:var(--ink); color:#fff; padding:28px 30px; margin-bottom:22px; }}
  .closing h2 {{ color:var(--orange); }}
  .closing p {{ font-size:15px; max-width:80ch; margin:0 0 14px; color:#e5e5e5; }}
  .closing strong {{ color:#fff; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; font-size:10px; text-transform:uppercase; letter-spacing:1px;
      color:var(--muted); border-bottom:1px solid var(--ink); padding:9px; }}
  td {{ padding:9px; border-bottom:1px solid #ededed; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  td.strong {{ font-weight:600; color:var(--ink); }}
  .mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
      color:var(--subtle); }}
  .badge {{ font-size:10px; padding:3px 9px; white-space:nowrap;
      text-transform:uppercase; letter-spacing:.6px; }}
  .badge-slip {{ background:#eef2f7; color:{DARK}; }}
  .badge-bad {{ background:rgba(254,80,0,.12); color:#b53a00; }}
  .badge-cancel {{ background:#f0f0f0; color:var(--muted); }}
  footer {{ color:var(--muted); font-size:12px; margin-top:36px;
      border-top:1px solid var(--line); padding-top:18px; max-width:90ch; }}
  @media (max-width:640px) {{
    .wrap {{ padding:24px 14px 48px; }} h1 {{ font-size:27px; letter-spacing:-.5px; }}
    section {{ padding:18px 16px 10px; }}
  }}
</style></head><body>

<div class="topbar"><div class="inner">
  <span class="eyebrow">Supply Chain Planning</span>
  <span class="who">Working prototype &middot; prepared by Brendan Meara</span>
</div></div>

<div class="wrap">

<header>
  <h1>Monthly S&amp;OP demand and material review</h1>
  <p class="sub">Project-level demand, material commitment and schedule realization,
  by region and market.</p>
  <div class="rule"></div>
</header>

<div class="synthetic">
  <strong>Synthetic data.</strong> The four region names and the North America revenue weighting
  are taken from Nextpower's public filings and public role titles, and the material rate is the
  one used in the case study I was sent. Every project, date and dollar figure below is generated
  to demonstrate a method. Nothing here represents the company's actual business, and this is not
  a Nextpower document.
</div>

<div class="decisions">
  <h2>Who this is for</h2>
  <p class="lede">Four functions consume this plan. Each arrives with a different question, and
  each answer is somewhere different in the page.</p>
  <table>
    <thead><tr><th>Consumer</th><th>The question they arrive with</th><th>Where it is answered</th></tr></thead>
    <tbody>
      <tr>
        <td class="who-role">Supply chain planning<span>owns the plan</span></td>
        <td class="q">Can I commit against this plan, and where do I put a small team of planners?</td>
        <td><a href="#sCAL">Calibration and bias</a>, and <a href="#s2">slip by market</a></td>
      </tr>
      <tr>
        <td class="who-role">Global steel sourcing<span>consumes the number</span></td>
        <td class="q">How much coil do I commit, for which months, and where is optionality worth
        paying for rather than committing firm?</td>
        <td><a href="#s1">Requirement as a range</a>, and <a href="#sPANEL">the same split by region</a></td>
      </tr>
      <tr>
        <td class="who-role">Regional supply chain<span>lives with the consequence</span></td>
        <td class="q">Does the global number represent my region, and what do I escalate this month?</td>
        <td><a href="#sBLEND">Market against regional blend</a>, and <a href="#sEXC">the exception list</a></td>
      </tr>
      <tr>
        <td class="who-role">Supply chain leadership<span>answers to finance</span></td>
        <td class="q">How much cash is committed against backlog that may not convert, and who
        chose that number?</td>
        <td><a href="#sSCEN">Realization as a control</a></td>
      </tr>
    </tbody>
  </table>
</div>

<div class="kpis">
  <div class="kpi"><div class="v">${open_material/1e6:,.0f}M</div>
    <div class="l">material against open backlog</div></div>
  <div class="kpi"><div class="v">${nxt.p50/1e6:,.0f}M</div>
    <div class="l">needed next month</div></div>
  <div class="kpi"><div class="v">{months_half_firm} months</div>
    <div class="l">at least half covered by booked orders</div></div>
  <div class="kpi"><div class="v">${at_risk/1e6:,.0f}M</div>
    <div class="l">of that, on open orders that have slipped</div></div>
</div>

<section id="s1">
  <h2>1 &nbsp;What to commit, as a range</h2>
  <p class="lede">Material required by month, in two layers. The grey area is demand from orders
  already on the books. The orange area is demand from bookings not yet placed. The dotted lines
  are the range the total is expected to fall within eight times out of ten.</p>
  {html['forward']}
  <p class="note">The mix changes across the horizon. Next month is {firm_first:.0f}% firm; twelve
  months out it is {firm_last:.0f}%. That is why the band widens from ${spread_first:.0f}M to
  ${spread_last:.0f}M, and it is the reason a single confidence level across the whole horizon
  does not work. The near months can be committed against; the far months are a forecast with a
  measured error rate.</p>
</section>

<section id="s2">
  <h2>2 &nbsp;Where the uncertainty comes from</h2>
  <p class="lede">Schedule slip from booked date to delivery, by market, across
  {len(orders):,} orders.</p>
  {html['slip']}
  <p class="note">The market-level structure here was specified when the data was generated, so
  this is a recovery test: the question is whether market-level aggregation recovers a structure
  that a regional average destroys. India is carried separately because its distribution sits well
  outside the others, at a P90 of {p90_market['India']:.1f} months against
  {p90_market['US / Canada']:.1f} for US and Canada.</p>
</section>

<section id="sBLEND">
  <h2>3 &nbsp;Why the regional average is the wrong unit</h2>
  <p class="lede">The same P90s, grouped by region, with the blended regional figure marked. In the
  two regions that contain more than one market, the regional number describes neither half of it.</p>
  {html['blend']}
  <p class="note">APAC blends to {p90_region['APAC']:.1f} months across Australia and South East
  Asia at {p90_market['Australia/SEA']:.1f} and India at {p90_market['India']:.1f}, with India at
  {india_share_apac:.0f}% of APAC bookings. EMEA blends to {p90_region['EMEA']:.1f} across Europe
  at {p90_market['Europe']:.1f} and MENA at {p90_market['MENA']:.1f}. A plan set on either regional
  figure runs early on one market and late on the other.</p>
</section>

<section id="sPANEL">
  <h2>4 &nbsp;The same requirement, split by region</h2>
  <p class="lede">Forward material requirement by region, with India dotted inside the APAC panel.
  Each region is on its own scale, because North America is roughly 69% of volume and flattens the
  other three when they share an axis. Compare shape and band width across panels, not height.</p>
  {html['regions']}
  <p class="note">Quantiles do not add. Summing the four regional P90s gives a figure
  {overstatement:.1f}% above the portfolio P90, because the regions do not all run late in the same
  month. The difference is material that would be bought and not needed. The size of that gap
  depends on an assumption worth stating: slip is drawn independently per order, with no shared
  shock. Real slip correlates through tariffs, interconnection queues and financing conditions, and
  under positive correlation this benefit shrinks. A production model would estimate that
  correlation rather than assume it away.</p>
</section>

<section id="sSCEN">
  <h2>5 &nbsp;Realization rate as a control</h2>
  <p class="lede">Material committed against open backlog at a given realization assumption.
  Move the slider.</p>
  {html['scenario']}
  <p class="note">The distance between the ends of the scale is the exposure the assumption
  controls. Setting it at 100% commits steel against projects that slip; setting it low risks
  dates on the projects that hold.</p>
</section>

<section id="sCAL">
  <h2>6 &nbsp;Was the forecast right, and by how much</h2>
  <p class="lede">Conversion lookback. What was expected to convert each month against what
  actually did, by region, with cumulative bias.</p>
  {html['lookback']}
  <p class="note">A region with persistent bias is one where the realization rate can be measured
  rather than chosen. Cumulative bias is the quantity that sets it, and it is also what downstream
  functions are already correcting for informally.</p>
  {html['coverage']}
  <p class="note">Walk-forward test. The stated 80% interval contained the outcome
  {raw_cov_all:.0f}% of the time across all cutoffs. Of the misses, {miss_high} fell above the
  interval and {miss_low} below.</p>
  <p class="note">{cal_verdict}</p>
  <p class="note">What this actually shows is the limit of the sample. Eight cutoffs over six
  months give {n_cal}+{n_test} observations, but they cover only {n_months} distinct outcome months,
  because overlapping windows score the same month more than once. A correction estimated from
  that cannot be expected to generalise, and the honest read is that the test says more about how
  much history you need than about whether the correction works. Two years of retained forecasts
  is roughly the minimum for this layer to mean anything.</p>
</section>

<section id="sEXC">
  <h2>7 &nbsp;What actually moved</h2>
  <p class="lede">Orders that slipped two months or more, or cancelled, ranked by material at
  risk.</p>
  <table>
    <thead><tr><th>Order</th><th>Region</th><th>MW</th><th>Expected</th><th>Actual</th>
      <th>Slip, mo</th><th>Reason</th><th>Material</th></tr></thead>
    <tbody>
{exceptions_table(exc)}
    </tbody>
  </table>
  <p class="note">The rows below are the largest by material at risk, drawn from every order
  in the book. The ${at_risk/1e6:,.0f}M tile at the top counts only orders still open at the
  cutoff, so it is a different population and the two will not reconcile by adding this column.</p>
</section>

<div class="closing">
  <h2>What this needs to run</h2>
  <p>None of it works without a record of expected conversion dates. Each order needs the date it
  was expected to convert, recorded when that date was set, and <strong>kept when the date
  moves</strong>. The lookback measures against that record.</p>
  <p>As you described the current process, those dates already exist in the pipeline and get
  overwritten each month rather than retained, so there is nothing to measure against. If that is
  right, keeping them costs very little and has to come before any modeling work.</p>
  <p>Without the record, the realization rate is a number someone picks and the interval has no
  tested coverage. Each function downstream then applies its own unstated discount to the plan.
  Measuring bias puts that correction in one place, where all four are working from the same
  number.</p>
</div>

<footer>
  Built from a project-level simulation with market-specific schedule slip, a Monte Carlo over the
  open order book, and split-conformal interval calibration. Python, pandas, NumPy, Plotly.
  Colours and type follow Nextpower's published web styling. Prepared by Brendan Meara on
  synthetic data; not a Nextpower document.
</footer>
</div></body></html>"""

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote", OUT)
    print("size %.1f KB" % (os.path.getsize(OUT) / 1024))


if __name__ == "__main__":
    main()
