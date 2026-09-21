# S&OP planning prototype

A working demonstration of a monthly demand and material review for a project-based
manufacturing business, built on simulated data.

Open `index.html` in a browser. The page is fully self-contained: Plotly is inlined, so
it renders offline and behind a proxy that blocks third-party CDNs. That is most of the
file size.

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

In this run the stated 80% interval contained the outcome 89.6% of the time across 48
scored forecasts, and the stated 50% interval contained it 72.9%. Of the misses, 3 fell
above the interval and 2 below. Both figures sit above nominal, but those 48 forecasts
cover only 13 distinct months, and against 13 effective observations neither gap is
distinguishable from chance. The interval is not demonstrably too wide; it is
demonstrably not too narrow.

Two limits on what the test covers, both stated on the page as well. It scores the
booked backlog only, because pipeline demand at a past cutoff cannot be checked against
the order book, and the published chart runs to twelve months where pipeline is most of
the median. And it runs at the same 4,000 draws as the published forecast rather than a
cheaper simulation, which it used to; at 800 draws two of the misses sat inside the
simulation's own noise.

Broken out by how far ahead the call was made, coverage is weakest one month out at
62.5% and sits at 100% from three months out. That is the reverse of the usual shape,
and the cause is drift rather than scatter. Sorted by cutoff, the month +1 error runs
`----++++`: the four earliest cutoffs forecast high by $2.8M on average and the four
most recent forecast low by $4.0M. A run that clean turns up by chance about 2.9% of
the time. The published median error at that horizon is near zero only because the two
halves cancel. A drifting front month is a tracking-signal problem with a known fix; a
wide front month would not be, and the two are indistinguishable in a coverage number.

There is no fitted correction here, and that is a decision rather than an omission. An
earlier version ran a split-conformal step to rescale the interval. The reason for
removing it is not the sample count: 48 forecasts across six horizons is 8 per horizon,
which is enough to construct an 80% bound. The reason is that the 48 are not 48
independent observations. A six-month window scores each outcome month from as many as
six different cutoffs, which is why 48 forecasts cover 13 months. Conformal calibration
assumes the calibration scores are exchangeable, scores sharing an outcome month are
not, and without exchangeability the guarantee fails at any sample size. More cutoffs
cut this way would not buy it back. Non-overlapping windows would, at roughly two years
of retained forecasts.

(The earlier version's multiplier also moved from 1.53x to 0.66x as the pipeline was
revised across versions. Both RNGs are seeded, so a given version reproduces exactly;
the movement was across versions, not across runs of one.)

## The Excel workbook

`Nextpower_SOP_Workbook_Meara.xlsx` carries the same analysis in the form most planning
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

## The Power BI project

`pbi/Nextpower SOP.pbip` is the same analysis as a semantic model rather than a
page: six tables in a star schema, nine relationships, and 36 DAX measures.

It is a `.pbip` rather than a `.pbix` because that format is text. The model is
readable JSON and the report layout is readable JSON, so both diff in git
instead of arriving as a binary blob nobody can review.

`Nextpower_SOP_PowerBI_Meara.pbix` is the same model with the data packaged
inside it. That is the file to open if you just want to use the thing: it needs
no CSVs, no refresh and no Python, and it works on a machine that has never seen
this repository.

The `.pbip` is the source. Open it in Power BI Desktop and **click Refresh
once**, because a project ships without cached data by design. Its CSV paths are
absolute, so re-run `powerbi_build.py` first if the repository lives somewhere
other than where it was generated.

The CSV paths are written into the queries absolutely. Re-running
`python powerbi_build.py` regenerates them for whatever machine it runs on. A
`DataFolder` parameter is the tidier form and was the first attempt, but Power
BI reported a cyclic reference and blocked every query that used it.

Four things the model does that the HTML page does not:

- **Role-playing dates.** `fact_orders` joins `dim_date` three times, on booked,
  promised and actual conversion. Two of those relationships are inactive and
  the measures reach for them with `USERELATIONSHIP`, so "booked in March" and
  "converting in March" are different questions against one table.
- **Percentiles that respond to the filter.** `Slip P90` is `PERCENTILEX.INC`
  over whatever is in context rather than a stored number, so slicing to a
  region or a status recomputes it.
- **The blend gap as a measure.** `Regional Blend P90` recomputes a market's P90
  at its region's grain, and `Blend Gap` is the distance. That is the section 3
  finding as something you can put on any visual.
- **A what-if parameter** on the realization rate: the native equivalent of the
  slider, driving three measures instead of one chart.

Measures are foldered (Volume, Backlog, Flow, Forecast, Slip, Realization,
Scenario, Coverage) and every one carries a description, so the field list
explains itself to someone who did not build it.

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

Brendan Meara
