# ExecutionLab

**Measure what it costs to turn a trading decision into a completed trade.**

ExecutionLab records public order books, estimates the cost of proposed order sizes,
and replays alternative execution schedules against the same market observations.
It connects short-horizon machine learning to a decision a trading desk actually
makes: how quickly to complete an order, at what cost, and with what completion risk.

The main experiment asks: **does an order-book forecast improve execution cost over
TWAP once fees, depth, latency, price limits, and unfinished quantity are included?**

Implemented in Python 3.11+ with NumPy and the standard library. It includes a
working Coinbase public-data recorder, a trained ridge model, four execution policies,
cost decomposition, stress experiments, provenance manifests, and 20 adversarial tests.
There are no credentials, account integrations, or order-entry endpoints.

## Why this belongs in the portfolio

Your other projects cover information credibility, return signals, volatility
forecasting, and native portfolio-risk computation. ExecutionLab supplies the next
layer: whether those decisions remain practical when you must actually trade them.

| Portfolio component | Existing work | ExecutionLab's contribution |
|---|---|---|
| Information | SignalGraph and Sentiment-Intel | A cost threshold against which to assess a proposed edge |
| Prediction | QuantPipeline and QuantTimeLearn | An economic decision test beyond forecast accuracy |
| Portfolio construction | QuantCUDA | A parent-order boundary for testing liquidity and execution |
| Implementation | ExecutionLab | Public data capture, cost curves, constrained child fills, and audit evidence |

These are architectural connections, not automatic integrations: existing equity
models do not become crypto strategies just because the recorder uses BTC-USD.
The public crypto venue makes order-book research accessible without a paid feed.

## What you can use today

- **Pretrade cost estimates:** inspect how spread, depth and assumed fees change
  the cost of buying or selling 0.01, 0.1, or 1.0 base units.
- **Schedule comparisons:** compare immediate execution, buffered TWAP, an
  Almgren–Chriss-inspired schedule, and forecast-adaptive execution on identical parents.
- **Transaction-cost analysis:** explain shortfall through delay, spread, book depth,
  fees, and opportunity cost of unfinished quantity.
- **Research validation:** save every forecast, decision, simulated fill, coefficient,
  input hash, and configuration so a surprising result can be investigated.

## First run

From this directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m execution_lab demo --output results/my_demo
```

The demo generates explicitly labeled synthetic order books and runs the entire
research pipeline. Every output directory must be new. Previous results are retained.

For this workspace, the supplied implementation was verified using the already
available Python 3.14.6 / NumPy 2.5.1 runtime:

```bash
make PYTHON=../QuantTimeLearn/.venv/bin/python test
```

## Record real public market data

```bash
.venv/bin/python -m execution_lab capture \
  --product BTC-USD --samples 1800 --interval 1 --depth 50 \
  --output data/captures/btc_session_01
```

This makes 1,800 request attempts, normally about 30 minutes plus network delays.
The recorder saves:

- `raw.jsonl`: exact HTTP response bodies, request/receive timestamps and selected headers.
- `snapshots.jsonl`: validated top-50 bid and ask levels with exchange and receive timestamps.
- `manifest.json`: source, configuration, accepted count, every rejected attempt, and hashes.

Duplicates, regressions, stale responses, auctions, crossed books, and malformed
levels are rejected. Public-data failures remain visible; the recorder never replaces
a failed live capture with synthetic prices. The capture's own `time` field and local
receive time expose delayed/cached responses.

Coinbase documents level 2 as aggregated depth: the published size already sums
orders at each price. The adapter therefore does **not** multiply it by order count.
These are independently polled snapshots, with expected sequence gaps, rather than
incremental order-book events. [Coinbase product-book documentation](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-book).

## Estimate a proposed trade's cost

```bash
.venv/bin/python -m execution_lab quote \
  --input data/captures/btc_session_01/snapshots.jsonl \
  --side buy --sizes 0.01 0.1 1.0 --fee-bps 5 --depth-fraction 0.10
```

The output is an **as-of-last-snapshot** estimate, including observed VWAP, available
quantity, unfilled quantity and one-way cost. The fee is your assumed scenario, not
a discovered exchange fee tier. The illustrative round-trip hurdle doubles entry
cost; actual exit conditions and impact still need modeling.

## Run the research experiment

```bash
.venv/bin/python -m execution_lab research \
  --input data/captures/btc_session_01/snapshots.jsonl \
  --config config/default.toml --output results/session_01

.venv/bin/python -m execution_lab stress \
  --input data/captures/btc_session_01/snapshots.jsonl \
  --config config/default.toml --output results/session_01_stress
```

The default experiment uses 300 warmup observations, up to 240 fully matured training
examples, a three-observation forecast horizon, a one-observation embargo, and
30-second parent deadlines. `config/smoke.toml` permits a short adapter check; it
is not a configuration for establishing research significance.

Stress retains five prespecified scenarios: base, 1,000 ms latency, 20 bp assumed fees,
2% available depth, and 5x parent size. Nothing selects the most favorable scenario.

## Algorithms and temporal discipline

The feature vector contains spread, five-level depth imbalance, weighted-mid offset,
sampled top-of-book order-flow imbalance, last return, trailing return volatility,
log depth and elapsed time between observations. OFI research motivates the feature;
because polling omits intermediate events, this implementation explicitly calls it
**sampled OFI**. [Cont, Kukanov and Stoikov](https://arxiv.org/abs/1011.6402).

A ridge model predicts the next three-observation mid-price return in basis points.
Features and labels are separated by availability. Each episode fits only labels
that matured before its start and embargo; normalization uses those training rows
only. Model parameters remain frozen throughout that parent order.

| Policy | Implemented behavior |
|---|---|
| Immediate | Request all remaining quantity at the next eligible execution observation; retry residuals |
| TWAP | Track a linear cumulative fill target with a buffer for the final child's arrival |
| AC schedule | Track a hyperbolic-sine inventory schedule with fixed, configurable urgency |
| Adaptive | Speed up or slow down the TWAP deficit using the signed ridge forecast |

The AC policy uses the classical schedule shape. Its urgency parameter is an assumed
scenario; it is not estimated from market impact or claimed as optimal for this
snapshot simulator. [Almgren and Chriss, original paper](https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf).

At arrival, the policy freezes a completion reserve equal to the trailing 95th-percentile
snapshot interval plus configured latency. Child orders set a slippage-bounded limit
price at decision time. They execute against the first later snapshot satisfying the
latency floor, consume at most the configured fraction of each observed level, and
cancel their unfilled remainder. Each subsequent child is a new decision. No passive
queue fills are inferred.

## Verified evidence

The local public capture collected **74 valid BTC-USD snapshots across 179.8 seconds**
from 180 request attempts. The other 106 attempts were rejected as duplicates/regressions.
The retained raw responses show the behavior rather than masking it.

The final short real-data replay contains seven parent time windows, each tested on
both buy and sell sides. All four policies completed all parents with the completion
reserve enabled. Mean simulated cost under the assumed 5 bp fee was:

| Policy | Mean shortfall, bps |
|---|---:|
| Immediate | 5.1170 |
| TWAP | 5.0770 |
| AC schedule | 5.0725 |
| Adaptive | 5.0739 |

Adaptive's advantage over TWAP was about **0.0032 bp**, an economically tiny difference
on an insufficient sample. The program declines to report a bootstrap interval with
fewer than eight paired time blocks. This establishes functioning capture, temporal
replay and accounting, not market-wide execution improvement.

The synthetic fixture contains 1,800 observations and 48 parent time windows. It
includes a controlled predictive relationship to test the forecast-to-policy path.
Its favorable numbers must remain labeled synthetic.

- [Final real-data report](results/final_live/REPORT.md)
- [Final synthetic report](results/final_demo/experiment/REPORT.md)
- [Final stress experiments](results/final_stress/README.md)
- [Validation details](docs/VALIDATION.md)
- [Research specification and next experiments](docs/RESEARCH.md)
- [Detailed interview guide](docs/INTERVIEW_GUIDE.md)

Earlier `verified_*` folders preserve development runs, including the original
deadline-completion failure. `final_*` folders contain the delivered implementation's
reference results. Raw capture files live under the gitignored `data/captures/` path;
copy or publish an explicitly selected capture separately when sharing the project.

## Project layout

```text
execution_lab/
  data.py          validated book schema, public recorder, integrity checks
  features.py      causal microstructure features
  model.py         purged, normalized ridge forecasting
  simulator.py     child scheduling, latency, limits, book sweeping, accounting
  pretrade.py      proposed-order cost and depth estimates
  research.py      paired evaluation, bootstrap, stress, reports, manifests
  synthetic.py     seeded mechanics fixture
  cli.py           capture / quote / research / stress / demo
config/            reproducible default and smoke configurations
tests/             adversarial accounting, temporal, data and workflow tests
docs/              methodology, operational limits and interview material
```

## Scope of the evidence

Exogenous replay cannot identify the effect of your own order on future prices or
replenishment. Visible depth is only an observation, hidden liquidity is omitted,
and REST sampling does not resolve the order of all intervening events. The depth
haircut is a stress assumption, not a participation rate or calibrated impact model.
Unknown/stale execution observations leave quantity unfilled and are recorded as
data-guard failures; they are not asserted to represent an actual exchange rejection.

A production extension would need event-stream reconstruction, more markets and
days, fill/impact calibration, and operational reliability work. The current useful
deliverable is an inspectable research and pretrade-analysis tool.
