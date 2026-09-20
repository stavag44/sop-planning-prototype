# S&OP planning prototype

A working demonstration of a monthly demand and material review for a project-based
manufacturing business, built on simulated data.

**[Open the dashboard](https://stavag44.github.io/sop-planning-prototype/)**

## What this is

Utility-scale equipment is sold as projects. Orders book months or years before they
ship, schedules slip, and material has to be committed against a date that has not
happened yet. A demand plan in that setting is a commitment decision, not a report.

This prototype takes 24 months of project-level bookings across four regions, each
market with its own schedule-slip behaviour, and produces what a monthly review would
actually work from:

- material requirement by month as a calibrated interval rather than a point number
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
worst cases overstates portfolio exposure by roughly 15% in this data. Variance adds, so
aggregate spread grows with the square root of the count, not the count.

**3. Calibration.** Split-conformal coverage check, walk-forward. It ignores what the
model claims about its own uncertainty and measures what actually fell inside the stated
interval. In this run the raw 80% interval covered 62% of months, conformal prescribed
widening by 1.53x, and calibrated coverage came to 83%.

## The Excel workbook

`SOP_data_and_calculations.xlsx` carries the same analysis in the form most planning
teams actually work in, and exists so the method can be audited without reading
Python. Ten tabs, from the raw order book through every derived field and aggregate
to the model outputs.

Everything Excel can compute is a live formula against the raw tab, not a pasted
value: material committed, schedule slip, status and material at risk per order;
SUMIFS aggregates for bookings, conversions, backlog and book-to-bill; array
percentiles for the slip distributions; the lookback with running bias; and the
commitment scenarios. Two tabs are marked as model output, because 4,000 simulations
across 895 orders and a walk-forward conformal calibration are not things to do in a
spreadsheet.

The Excel figures reconcile to the Python ones exactly. Slip P90 by market comes out
1.76, 3.35, 7.40, 4.08, 9.82 and 3.56 months in both, and the conformal widening
factor is 1.47 in both.

## Running it

```
pip install -r requirements.txt
python simulate.py      # generates orders, monthly aggregates, lookback, exceptions
python montecarlo.py    # forward simulation + conformal calibration
python dashboard.py     # writes index.html
```

Each script writes to `data/` and prints its headline numbers. `simulate.py` is seeded,
so the figures reproduce.

## Files

| | |
|---|---|
| `simulate.py` | project-level booking and slip generation, four regions, six markets |
| `montecarlo.py` | forward Monte Carlo over open backlog, conformal calibration |
| `dashboard.py` | builds the self-contained HTML page |
| `data/` | generated CSVs, regenerable from the scripts |
| `index.html` | the dashboard |

Plotly loads from a CDN, so the page needs a network connection to render.

Brendan Meara
