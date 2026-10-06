# Research design and data contract

## Decision and hypothesis

A parent order specifies a product, side, base quantity, arrival time, and completion
deadline. The decision is how much of the residual quantity to request at each
observation. Price limits and available displayed depth constrain the resulting fill.

The primary hypothesis is that a short-horizon microstructure forecast can improve
the cost/completion tradeoff over a linear time schedule. Forecast RMSE is secondary:
predicting price more accurately does not establish cheaper execution.

Primary comparison: adaptive minus TWAP implementation shortfall for the same parent
window and side. Negative values favor adaptive. Report completion for the entire
sample before looking at completed-pair cost comparisons. Excluding incomplete
pairs makes the paired comparison conditional and can introduce selection effects;
it must not replace the full policy-level table.

## Observation contract

Each normalized JSONL row includes:

| Field | Meaning |
|---|---|
| `product` | Venue product such as BTC-USD |
| `received_ns` | Local wall-clock UTC nanoseconds when HTTP response was received |
| `exchange_ns` | Exchange timestamp from the response |
| `sequence` | Exchange book sequence |
| `bids`, `asks` | Ordered `[price, base_quantity]` levels, already aggregated |
| `source` | `coinbase_rest` or explicitly `synthetic` |

Use receive time as the earliest local availability. Exchange time is useful for
age checks; it must not backdate the researcher's knowledge. Local clock skew greater
than the allowed tolerance is rejected. System-clock synchronization remains an
operational requirement; the capture does not estimate clock offset.

The recorder stores raw response text as a string inside a JSONL envelope with
request and receive timestamps. Headers include Date, Age and Cache-Control when
present. Raw and normalized data are hashed at completion. Exceptions and rejections
are listed in the capture manifest. Files flush per accepted snapshot; this does
not promise database transactions or durability through a hardware failure.

Normalized records require increasing receive times and sequences, finite positive
prices and quantities, unique ordered levels, and an uncrossed book. Multiple
products or real/synthetic source tags in one file are rejected. Sequence jumps are
expected with snapshot polling; they do not establish dropped incremental events.

For locally captured input, the research runner verifies both raw and normalized
file hashes against the capture manifest. This proves local consistency, not
cryptographic authenticity of the exchange response. External source declarations
without the raw capture are labeled `external_unverified` in the result manifest.

## Features and model

At each observation, calculate eight features:

1. Spread in basis points relative to mid.
2. Bid-minus-ask five-level depth divided by total five-level depth.
3. Weighted-mid offset: `(ask*bid_size + bid*ask_size)/(bid_size+ask_size) - mid`.
4. Top-of-book sampled OFI using consecutive observed prices and sizes.
5. Previous-to-current mid return in basis points.
6. Standard deviation of up to 30 preceding sampled returns.
7. `log(1 + total five-level depth)`.
8. Seconds since the preceding received observation.

The weighted-mid feature is a simple imbalance-weighted quote. It is not a fitted
multi-step microprice estimator. Full event OFI is not recoverable from two snapshots.

The target for observation `i` is `10,000 * (mid[i+h]/mid[i] - 1)` with `h=3` by
default. The horizon is explicitly in observations, so elapsed time varies with the
feed. At parent start `s`, eligible labels satisfy `i+h < s-embargo`; `embargo=1`.
The last 240 eligible rows are used by default. At least 20 eligible rows are required.

Training features are standardized with their own mean and standard deviation.
Ridge solves `(Z'Z + alpha*I) beta = Z'(y - mean(y))` with an unpenalized intercept.
The model and scaler freeze for the parent lifetime. The next parent refits on its
own matured past. Defaults are fixed; no hyperparameter search is hidden in the run.

Why ridge first: small samples, correlated book features, transparent coefficients,
and inexpensive repeated fits. A neural sequence model is an experiment to earn by
beating this baseline on held-out economic outcomes, not a required badge of complexity.

## Scheduling

All policies share parent quantity, latency floor, fee, depth availability, slippage
limit, deadline, and data guards. Buy and sell simulations are separate counterfactuals
against the same observations; they do not interact with each other.

At arrival, estimate a completion reserve from the last up to 30 observed snapshot
intervals: their 95th percentile plus the latency floor. Planning ends before the
parent deadline by this amount. The initial slice receives one median interval of
time credit. This buffered TWAP design is explicit because a final child sent at the
deadline cannot arrive before it.

The immediate policy requests the full residual. TWAP requests its positive deficit
to a linear cumulative target. The AC-inspired policy replaces that target with
`Q * [1 - sinh(k*(1-f))/sinh(k)]`, where `f` is elapsed planning fraction and `k` is
fixed urgency. At `k=0`, the limit is TWAP; increasing urgency front-loads the schedule.
The schedule follows the shape derived in the classical execution literature; the
project does not calibrate impact coefficients or solve its own nonlinear replay
objective. [Almgren and Chriss](https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf).

Adaptive multiplies the TWAP deficit by
`1 + strength*tanh(side_sign*forecast_bps/alpha_scale_bps)`. With strength 0.5, the
multiplier lies between 0.5 and 1.5. A buyer accelerates before predicted rises; a
seller accelerates before predicted falls. Once the planning window ends, adaptive
requests the entire residual. Every request is capped by the outstanding parent.

## Execution semantics

At decision time, a child fixes a limit price at current mid plus/minus the configured
slippage budget. The default is 20 bps. Wide/stale decision books block submission.
The child's earliest execution is decision receive time plus latency. Replay finds
the first strictly later observation satisfying that time, without interpolating
unseen prices. Execution after the parent deadline is disallowed.

The child sweeps the opposing side in price order up to its fixed limit. At each
level, only `depth_fraction * displayed_quantity` is available. The unused child
quantity cancels, and a later observation can produce a new child. A given parent
never executes more than one child against the same snapshot.

The recorder uses aggregated depth. It cannot establish queue position, cancellation
priority, maker fills, trade participation, permanent impact, or liquidity that would
have existed after the hypothetical order. Reusing later exogenous snapshots implicitly
permits replenishment and does not preserve the parent's consumed depth across time.

If the first eligible execution observation is too old or the unseen gap exceeds
the configured maximum, replay records `unobservable_execution` and leaves the
quantity unfilled. This is a limitation of the evidence, not a simulated claim that
a real submitted order would have received a reject. Always report guard incidence.

## Accounting identities

Let `s=+1` for a buy and `s=-1` for a sell, parent size `Q`, arrival mid `P0`, fill
quantities `qj`, prices `pj`, fill-time mids `mj`, and side touches `tj`.

Filled execution cost in quote currency:

```text
executed_cost = s * sum_j qj*(pj-P0) + sum_j fees_j
delay        = s * sum_j qj*(mj-P0)
spread       = s * sum_j qj*(tj-mj)
depth        = s * sum_j qj*(pj-tj)
executed_cost = delay + spread + depth + fees
```

Divide each component by `Q*P0`, then multiply by 10,000 for parent-notional bps.
The simulator asserts the additive identity on every replay. Unlike a filled-only
VWAP metric, this denominator stays consistent when completion differs.

For residual `R`, add `s * R * (terminal_mid-P0)` as opportunity cost. The terminal
mid is the last observed mid at or before the known deadline. This mark is not a
liquidation price; no exit spread, fee, or invisible depth is invented. Incomplete
orders can appear cheaper simply because less quantity paid fees. Report their fill
rate and opportunity mark beside realized cost, and retain conditional-pair warnings.

Five-second markout is `s*(future_mid-fill_price)/fill_price * 10,000`, when a later
observation exists between five and ten seconds after execution. Positive values
indicate favorable subsequent movement for the executed side. These values are
post-trade diagnostics, inaccessible to the policy.

## Evaluation and uncertainty

Parent time windows do not overlap. For each start, run both sides and all policies
on the same source observations. Pair adaptive with TWAP by start and side. For
completed pairs, average buy/sell differences at each time window before calculating
uncertainty, since the two sides share the market path.

The interval is a seeded moving-block bootstrap across those time-window differences,
with block length four by default. Fewer than eight paired time windows produce no
interval. Eight is only a mechanical threshold; it does not make eight windows an
adequate sample. Adjacent windows can remain dependent, and bootstrap uncertainty
within one short session does not measure cross-day regime uncertainty.

The regression diagnostic reports RMSE and R² against zero future mid change on one
forecast per episode. It does not substitute the direction hit rate for economic
execution performance. No statistical claim is derived from the short live check.

## Prespecified extension study

Before comparing stronger models, freeze a multi-session protocol:

1. Collect BTC-USD and ETH-USD in separate files across at least several weeks and
   multiple UTC sessions. Preserve network failures, book age, and sampling statistics.
2. Convert model targets to fixed elapsed-time horizons, with explicit missing-label
   rules. Migrate to event streaming before investigating subsecond effects.
3. Use chronological training, a separate tuning period, and untouched later days for
   final evaluation. Separate products when claiming cross-product generalization.
4. Fix parents from observable size/depth buckets and prespecify buy/sell schedules.
   Compare immediate, TWAP, AC schedule, adaptive, and strength-zero ablation.
5. Retain fees, latency, depth, and size stress; report completion and data exclusions.
   Cluster uncertainty by capture day once enough distinct days exist.
6. Require improvement larger than model and fee uncertainty. A tiny bps difference
   on a small sample is not a useful trading result.
7. Only then test nonlinear predictors, passive orders with defensible queue data,
   and an empirically calibrated temporary/permanent impact model.

These are future research tasks. The delivered code implements the baseline pipeline,
not an already-completed multiweek study or a calibrated execution service.

## Sources and interpretation

- [Coinbase level-2 snapshot contract](https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-book): endpoint semantics and aggregation.
- [Cont, Kukanov and Stoikov, The Price Impact of Order Book Events](https://arxiv.org/abs/1011.6402): motivation for order-flow imbalance and market depth.
- [Almgren and Chriss, Optimal Execution of Portfolio Transactions](https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf): classical inventory schedule and cost/risk tradeoff.

The choices of ridge penalty, adaptive multiplier, completion reserve, guards, snapshot
haircut, and experiment size are this project's engineering assumptions, not fitted
facts taken from these sources.
