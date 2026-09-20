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

- material requirement by month as a calibrated interval rather than a point number, split
  into orders already booked and bookings not yet placed
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

**3. Calibration.** Split-conformal coverage check, walk-forward, with a genuine
holdout. It ignores what the model claims about its own uncertainty and measures what
actually fell inside the stated interval.

The split matters. Fitting the multiplier on the same points that then score it makes
the reported coverage a restatement of the chosen rank rather than a result. Here the
earliest five cutoffs fit the correction and the three most recent score it. The score
is signed rather than absolute, so the correction can shift the centre as well as widen
the band.

In this run the stated 80% interval covered 73% of month-forecasts across all cutoffs.
Of the misses, 7 fell above the band and 6 below, close to two-sided, so the fitted
correction barely moves the centre and mostly widens: 1.46x. On the held-out cutoffs the
raw interval covered 83% and the calibrated interval also covered 83%. Widening did not
improve held-out coverage on this sample, because those misses fell well outside the
band rather than just beyond it. With 18 held-out observations that estimate is noisy,
and reporting it as a clean improvement would overstate what a sample this size can
show.

## The Excel workbook

`SOP_data_and_calculations.xlsx` carries the same analysis in the form most planning
teams actually work in, and exists so the method can be audited without reading
Python. Ten tabs, from the raw order book through every derived field and aggregate
to the model outputs.

Everything Excel can compute is a live formula against the raw tab, not a pasted
value: material committed, schedule slip, status and material at risk per order;
SUMIFS aggregates for bookings, conversions, backlog and book-to-bill; array
percentiles for the slip distributions; the lookback with running bias; and the
commitment scenarios, including the conformal calibration, which is worked in full. The
one thing Excel cannot reasonably do is run 4,000 simulations across the order book, so
the P10/P50/P90 columns on the two MC tabs are simulated values. Everything derived from
them is a formula.

The Excel figures reconcile to the Python ones exactly, including the conformal step:
the two ranked scores, the band width, and both held-out coverage figures come out the
same in the spreadsheet as in the model.

## Running it

```
pip install -r requirements.txt
python simulate.py      # generates orders, monthly aggregates, lookback, exceptions
python montecarlo.py    # forward simulation + conformal calibration
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
| `montecarlo.py` | forward Monte Carlo over open backlog, conformal calibration |
| `dashboard.py` | builds the self-contained HTML page |
| `build_workbook.py` | builds the Excel workbook |
| `check_render.py` | fails the build if any chart carries no data |
| `data/` | generated CSVs, regenerable from the scripts |
| `index.html` | the dashboard |

Plotly loads from a CDN, so the page needs a network connection to render.

Brendan Meara
