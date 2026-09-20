# S&OP planning prototype

A working demonstration of a monthly demand and material review for a project-based
manufacturing business, built on simulated data.

Open `index.html` in a browser. The page is self-contained apart from the Plotly library.

## What this is

Utility-scale equipment is sold as projects. Orders book months or years before they
ship, schedules slip, and material has to be committed against a date that has not
happened yet. A demand plan in that setting is a commitment decision, not a report.

This prototype takes 24 months of project-level bookings across four regions, each
market with its own schedule-slip behaviour, and produces what a monthly review would
actually work from:

- material requirement by month as an interval rather than a point number, split into
  orders already booked and bookings not yet placed
- schedule slip by market, with outlier markets tracked separately from their region
- committed material against backlog at a chosen realization rate, as a control
- a conversion lookback measuring expected against actual, with cumulative bias
- an order-level exception list ranked by material at risk

## Synthetic data

Every project, date and dollar figure is generated. Region names and the North America
revenue weighting are drawn from public filings and public role titles at a solar
tracker manufacturer; nothing else corresponds to any real company's business. The page
is styled to that company's published web palette and carries a byline saying it is not
their document.

The point is the method. Accuracy against a real business was never the claim.

## Method

Three layers, deliberately separated so the replaceable one is obvious.

**1. Slip model.** Where each order's schedule-slip distribution comes from. Here it is
empirical resampling from realised history, conditioned on market rather than region,
because a regional average hides an outlier market inside it. In production this is where
a fitted quantile regression goes, conditioned on pipeline stage, interconnection status,
developer identity and project size. `EmpiricalSlipModel` exposes a `sample()` interface
so a fitted model drops in without touching anything downstream.

**2. Aggregation.** Monte Carlo over the open order book. This is necessary because
quantiles do not add: the P90 of a sum is not the sum of P90s, and adding per-region
worst cases overstates portfolio exposure by about 16% in this data.

That figure depends on an assumption the model makes and a real business would not:
slip is drawn independently per order, with no shared shock. Variance adds *under
independence*, which is what makes aggregate spread grow with the square root of the
count. Real slip correlates through tariffs, interconnection queues and financing
conditions, and under positive correlation the diversification benefit shrinks. A
production model would estimate that correlation rather than assume it away.

**3. Coverage test.** A walk-forward check of whether the interval means what it says.
The model is re-run at eight past cutoffs using only what was known at each one, and the
six months of forecast that follow each cutoff are scored against what the month turned
out to be. It ignores what the model claims about its own uncertainty and measures what
actually fell inside the stated range.

In this run the stated 80% interval contained the outcome 87.5% of the time across 48
scored forecasts, and the stated 50% interval contained it 68.75% of the time. Of the
misses, 4 fell above the interval and 2 below. Broken out by how far ahead the call was
made, coverage is weakest one month out at 62.5% and sits at or above 87.5% from three
months out. That is the reverse of the usual shape. Near-in months are carried by a
handful of large orders, and one of them moving is enough to put the month outside the
band.

There is no fitted correction here, and that is a decision rather than an omission. An
earlier version ran a split-conformal step to rescale the interval. With 48 scored
forecasts spread across six horizons it had roughly five observations per horizon to
estimate a quantile from, which is below what the method needs to be valid, and its
answer swung from 1.53x to 0.66x across three runs of the same pipeline. That is noise,
not a finding. Measuring coverage and reporting it is the part the sample supports.
Correcting on it would need about two years of retained forecasts.

## The Excel workbook

`SOP_data_and_calculations.xlsx` carries the same analysis in the form most planning
teams actually work in, and exists so the method can be audited without reading
Python. Ten tabs, from the raw order book through every derived field and aggregate
to the model outputs.

Everything Excel can compute is a live formula against the raw tab, not a pasted
value: material committed, schedule slip, status and material at risk per order;
SUMIFS aggregates for bookings, conversions, backlog and book-to-bill; array
percentiles for the slip distributions; the lookback with running bias; the commitment
scenarios; and the whole coverage test, hit-or-miss flags and summary both. The one
thing Excel cannot reasonably do is run 4,000 simulations across the order book, so the
P10/P50/P90 columns on the two MC tabs are simulated values. Everything derived from
them is a formula.

The Excel figures reconcile to the Python ones exactly, coverage test included: the
observation count, the distinct-month count, both interval coverage figures, the miss
counts and the per-horizon breakdown come out the same in the spreadsheet as in the
model.

## Running it

```
pip install -r requirements.txt
python simulate.py      # generates orders, monthly aggregates, lookback, exceptions
python montecarlo.py    # forward simulation + walk-forward coverage test
python dashboard.py     # writes index.html
python check_render.py  # verifies every chart actually carries data
python build_workbook.py
```

Each script writes to `data/` and prints its headline numbers. `simulate.py` is seeded,
so the figures reproduce.

## Files

| | |
|---|---|
| `simulate.py` | project-level booking and slip generation, four regions, six markets |
| `montecarlo.py` | forward Monte Carlo over open backlog, walk-forward coverage test |
| `dashboard.py` | builds the self-contained HTML page |
| `build_workbook.py` | builds the Excel workbook |
| `check_render.py` | fails the build if any chart carries no data |
| `data/` | generated CSVs, regenerable from the scripts |
| `index.html` | the dashboard |

Plotly loads from a CDN, so the page needs a network connection to render.

Brendan Meara
