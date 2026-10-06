# Connecting execution research to the portfolio

The practical interface is a parent-order instruction. An upstream model decides
whether it wants an exposure; execution research estimates whether that exposure
can be acquired at acceptable cost and within its deadline.

## Proposed boundary

```text
Upstream decision:
  instrument, venue, side, base_quantity, decision_available_at,
  deadline, expected_edge_bps, maximum_execution_cost_bps,
  decision_id, model_vintage

Execution analysis:
  as_of_book, proposed_schedule, estimated_available_quantity,
  expected_spread_and_depth_cost, assumed_fee,
  simulated_shortfall, completion_fraction, failure_reasons
```

ExecutionLab's current CLI accepts one product capture and a scalar parent size in
configuration. The full multi-order contract above is an integration design, not an
already implemented portfolio import.

## Mapping each existing project

| Project | Useful output | Additional information required |
|---|---|---|
| QuantPipeline | Target equity weights and next-period return forecasts | Portfolio NAV, current positions, instrument metadata, equity venue depth, decision timestamp |
| QuantTimeLearn | Forecast volatility and desired exposure scale | Instrument mapping, risk-adjustment deadline, position units, compatible market data |
| QuantCUDA | Constrained portfolio weights/candidates | Current holdings, parent generation, symbol/venue mapping, lot constraints, execution budget |
| SignalGraph | Evidence-backed credibility and directional research hypotheses | A separately evaluated trading rule; confidence alone is not expected return |
| Sentiment-Intel | Watchlist rankings and content availability | Calibrated expected returns; the current score-to-return mapping is heuristic |

Do not apply the existing US-equity models to BTC simply to make an integration demo.
Either collect equivalent equity market data or develop a separately evaluated
crypto decision model. Schema reuse does not validate a change in asset class.

## A useful pretrade question

Suppose a model estimates an 8 bp gross edge. If realistic entry plus exit execution
costs exceed 8 bps, trading the signal is unattractive even before risk or forecast
uncertainty. ExecutionLab can estimate the observed entry spread/depth cost and expose
the fee assumption. Exit cost, impact, and calibrated forecast uncertainty still
need separate estimates.

For scale, a hypothetical 1 bp cost difference on $100 million of traded notional
is $10,000. That arithmetic explains why execution matters; it is not a saving
demonstrated by this project's small capture.

## A concrete demonstration for an interview

1. Open the real-data report and identify data provenance, quantity and fee assumptions.
2. Run the `quote` command at increasing sizes and explain why the largest request
   may exceed the retained, haircutted depth.
3. Open a parent in `parents.csv` and reconcile delay, spread, depth and fees.
4. Find its children in `fills.csv`; point to decision/arrival timestamps and partial fills.
5. Inspect the matching decisions and frozen model label cutoff.
6. Show the original missed-deadline result and explain the causal completion reserve.
7. Finish with the multiweek held-out experiment needed to make a performance claim.

This connects research, engineering and economic judgment through evidence someone
else can reproduce.
