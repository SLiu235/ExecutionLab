# ExecutionLab — portfolio and interview guide

## Project in one sentence

Built an auditable execution research system that turns public order-book data into
pretrade cost estimates and paired, latency-aware tests of classical and learned
execution schedules.

## 30-second answer

> My portfolio already covered signal research and portfolio risk, so I built the
> execution layer. ExecutionLab records real public order books and tests how much
> it costs to complete the same parent trade using immediate execution, TWAP, a
> classical inventory schedule, or an order-book forecast. I implemented timestamp
> validation, a purged ridge model, latency and price limits, depth-aware partial
> fills, and an exact shortfall decomposition. I verified the whole path on a live
> BTC-USD capture and wrote adversarial tests for accounting and future-data leakage.

## Why a quant team might care

A researcher can report a small predictive edge and still lose money after trading.
A portfolio optimizer can request a position that is too large for available
liquidity. An execution algorithm can look cheap because it leaves the difficult
part of the order unfinished. Those are practical failures a desk needs to measure.

ExecutionLab makes each issue inspectable. The pretrade command shows a size-dependent
cost estimate and residual quantity. Replay fixes the parent instruction across
policies. Its report displays completion, cost, and every rejected or unobservable
execution attempt together. It separates forecast quality from the value of the
decision made with that forecast.

This gives you a more credible engineering story than merely adding a more complex
predictor: you can explain data uncertainty, execution mechanics, economic accounting,
and the boundaries of an experiment using actual artifacts.

## The five-minute technical walkthrough

### 1. Start with the data problem

The recorder requests public Coinbase level-2 snapshots without an account. It saves
the raw response and local receive time before normalization. The first three-minute
capture had 180 requests but only 74 fresh, accepted books; 106 were rejected as
duplicates/regressions. That is why I did not assume one request meant one independent
market observation.

I use receive time for feature availability and exchange time for age checks. I check
ordering, sequence monotonicity, book crossing, finite positive levels, auctions and
source consistency. I preserve rejections in the manifest. Hashes connect the raw
capture, normalized data and experiment outputs.

### 2. Explain the model precisely

I predict a three-observation future mid-price return using eight microstructure
features: spread, five-level depth imbalance, imbalance-weighted mid offset, sampled
OFI, previous return, rolling volatility, log depth and elapsed observation time.

I fit ridge regression because the features are correlated, data is limited, and the
model is cheap and easy to inspect. For each parent start, I purge labels whose future
endpoint is not yet available, leave an embargo, fit scaling on training rows alone,
and freeze the model until that parent ends. A simple model is a useful baseline
because a claimed execution benefit must survive the economic test first.

### 3. Explain the action

A parent order is an instruction to buy or sell a fixed quantity before a deadline.
TWAP tracks a linear cumulative target. The AC-inspired policy front-loads the target
through a hyperbolic-sine inventory schedule. Adaptive modifies TWAP's remaining
deficit using its signed forecast: buy faster before expected rises, sell faster
before expected falls.

All policies receive the same parent size, fee assumption, latency, deadline and
depth availability. Each child sets its limit at decision time. A later price may
change its fill, but cannot rewrite the earlier requested quantity or limit.

### 4. Explain the simulator's boundaries

The simulator evaluates aggressive immediate-or-cancel-style children against the
next eligible recorded snapshot. It sweeps observed levels only and retains partial
fills. No passive fill is inferred from a candle touching a price. The latency
setting is a floor: the sampling interval may make observed execution much later.

The difficult boundary is counterfactual impact. My hypothetical trade would change
the actual book, but the recorded future book comes from a market where that trade
did not happen. I expose that assumption and stress size and available depth; I do
not call the replay a calibrated causal impact model.

### 5. End with evidence

The implementation passed 20 tests. The real capture replay covered seven parent
windows on both sides, with all parents completed by all policies after adding the
completion buffer. Average costs were about 5.07–5.12 bps under an assumed 5 bp fee.
Adaptive's difference from TWAP was about 0.0032 bp, too small and too lightly sampled
to establish a useful improvement. The engineering result is that I can reproduce,
decompose and challenge the outcome, including a negative or insignificant result.

## Algorithms to know at the whiteboard

**Implementation shortfall.** For a buy, paying above the arrival mid is costly;
for a sell, receiving below it is costly. Add actual simulated fill fees and normalize
by original parent notional. Unfilled quantity receives a separate terminal-mid
opportunity mark. Completion remains a separate metric.

**Cost decomposition.** Add and subtract the execution mid and touch from the fill
price. The result separates market movement during the delay, crossing the spread,
walking beyond the touch, and fees. This is an algebraic identity the code asserts.

**Ridge regression.** Standardize on past data, solve the regularized least-squares
normal equations, and preserve the scaler with the coefficients. The L2 penalty
stabilizes correlated predictors. The intercept is not penalized.

**Temporal purging.** A sample's feature date being in the past is insufficient.
Its future-return label must also have matured. Record the maximum label-end index
with each fitted model and reject any model that crosses the parent start.

**Inventory schedules.** TWAP distributes the quantity over time. The classical
schedule used here is more front-loaded as urgency rises and converges to TWAP at
zero urgency. Its parameter is fixed; impact calibration remains future work.

**Paired moving-block bootstrap.** Compare policies on the same parent rather than
unpaired days. Combine buy/sell results from the same start before resampling because
they share a path. Resample adjacent blocks to retain some serial dependence. A
short within-session interval cannot prove stability across different market days.

## The strongest debugging story

> The first real-data run had low completion for scheduled policies even with a
> small parent size. It initially looked like a liquidity problem, but the immediate
> policy filled fully, and the decision audit showed late children. The capture's
> valid observations arrived more slowly than the request interval because many
> responses repeated an earlier book. TWAP kept working toward its target too close
> to the deadline, leaving insufficient time for the next observed execution.
>
> I added a reserve based on the 95th percentile of sampling intervals observed
> before the parent arrived, plus the configured latency. I froze that reserve at
> arrival to prevent future-data leakage and tested that changing future receive
> times did not change the initial requested quantity. All policies then completed
> the short replay. I retained both versions' artifacts, because the fix addresses
> execution mechanics and must not be presented as a newly discovered market edge.

This story demonstrates the distinction between a request cadence, an observation
cadence, and an execution deadline. It also shows that a research problem can be an
infrastructure problem before it is a modeling problem.

## Questions an experienced interviewer may ask

**Why crypto if your other projects are equities?**

It provides accessible public depth for validating the plumbing without a paid feed.
Execution principles transfer; measured fees, depth, latency and market behavior do
not. An equity extension needs its own venue data, trading calendar, tick size,
lot size, routing and cost model. I have not imported equity signals into BTC trades.

**Why not use reinforcement learning?**

An RL policy can exploit mistakes in a simulator more efficiently than a simple
heuristic. I first need credible fills, economic accounting, and strong baselines.
RL becomes justified when the environment models action effects and the learned
policy beats prespecified benchmarks on unseen market days and stress regimes.

**Is this a limit-order-book matching engine?**

It is a snapshot execution replay with book sweeping. It does not track every
individual order or infer maker queue position. The scope follows the data available.

**Is your weighted-mid feature Stoikov's calibrated microprice?**

No. It is a top-of-book imbalance-weighted quote. The feature name refers to its
offset from mid; a fitted multi-step microprice estimator would be a separate model.

**Why not use VWAP or participation-of-volume as a benchmark?**

I do not have a validated trade-volume feed here. Visible depth is a stock of
liquidity, not traded volume. A claimed participation rate based on depth would be
misleading. Add synchronized trades and validate their coverage before adding POV.

**What does the depth fraction mean?**

It is an assumed haircut applied to each displayed price level. It tests the
sensitivity of the result to liquidity availability. It is neither a fitted fill
probability nor a percentage of market volume.

**How do you prevent an incomplete strategy from looking best?**

Report fill and completion rates, mark residual opportunity cost, and compare costs
on identical completed pairs. I also disclose the conditional selection in that
comparison. A production objective needs an agreed penalty or a credible forced
completion mechanism for remaining quantity.

**What if the feed disconnects after a child decision?**

The replay flags unobservable execution rather than inventing a price. This leaves
quantity unfilled in the experiment. Real order state would be unknown and would
require execution reports and reconciliation. That operational layer is not modeled.

**Does passing the tests prove the strategy works?**

It proves specific mechanical properties: accounting, temporal independence,
limit-setting, partial fills, data rejection, repeatability and manifest consistency.
Economic usefulness requires substantially longer untouched real-data evaluation.

## Tech stack and ownership

Python 3.11+, NumPy, dataclasses, urllib, JSONL, TOML, CSV, SHA-256 and unittest.
The core research implementation uses no black-box backtesting framework. The main
ownership areas are data contracts, model availability rules, execution accounting,
policy comparison and adversarial validation. Avoid claiming C++, CUDA, exchange
connectivity certification or production trading capabilities for this project.

## Resume bullets

Choose one or two that fit the role:

- Built a public order-book capture and execution research pipeline with timestamp
  validation, raw-data provenance, depth-aware child fills, latency floors and
  decision-time price limits; verified end to end on real BTC-USD observations.
- Implemented purged walk-forward ridge forecasts and compared adaptive execution
  with TWAP and classical inventory schedules using paired shortfall, completion,
  fee/depth/latency stress and reproducible experiment manifests.
- Added 20 adversarial tests covering future-data perturbations, label maturity,
  exact buy/sell cost decomposition, partial fills, stale data, price limits and
  raw-capture integrity.

Do not insert a percentage cost improvement into the bullet. The current live
sample is an engineering verification, and the observed difference is tiny.

## The next portfolio milestone

The most convincing next artifact would be a fixed-protocol report over several
weeks of public captures showing which liquidity conditions make adaptive execution
useful, which make it worse, and how much of the difference remains after uncertainty
and cost assumptions. Keep a simple model until the data demonstrates a reason to
add complexity. A clear, reproducible failure analysis can be more informative to an
interviewer than an unexplained high Sharpe ratio.
