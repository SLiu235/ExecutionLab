# ExecutionLab — experiment report

Data: **coinbase_rest** · BTC-USD · 74 validated snapshots · 179.8 seconds observed.

7 non-overlapping parent time windows; independent buy and sell replay on each window. Same parent size, deadline, fee and visible-depth limits for every policy.

Parent: 0.01 base units; deadline: 15.0s; assumed fee: 5.0 bps; latency floor: 250.0ms; available depth haircut: 10%.

| Policy | Completion | Average filled | Mean marked cost (bps) | P95 cost (bps) |
|---|---:|---:|---:|---:|
| immediate | 100.0% | 100.0% | 5.1170 | 5.5275 |
| twap | 100.0% | 100.0% | 5.0770 | 6.0874 |
| ac_schedule | 100.0% | 100.0% | 5.0725 | 5.8372 |
| adaptive | 100.0% | 100.0% | 5.0739 | 6.0720 |

Lower cost is better. Marked cost includes terminal-mid opportunity cost on unfilled quantity; it is not a forced-completion price. Completion must be assessed alongside cost.

Adaptive minus TWAP, on fully completed paired time blocks: **-0.0032 bps**. Included blocks: 7; excluded for noncompletion: 0.

Paired 95% interval: None. insufficient independent time coverage.

Frozen ridge forecast RMSE: 0.6792 bps; zero-change baseline: 0.7168 bps over 7 future-return labels.

## Interpretation

The synthetic fixture tests accounting and temporal mechanics. A short real capture tests the adapter and replay path. Neither establishes execution improvement across market regimes.

Replay assumes aggressive orders against recorded depth, exogenous future books, and no permanent/transient own-order impact. It provides no queue position, passive fill, cross-venue routing, or event-complete OFI claim. Repeated snapshots can replenish simulated depth independently of the hypothetical parent; the depth haircut and size stress expose sensitivity but cannot repair that counterfactual assumption.

Latency is a minimum: orders resolve against the first later available snapshot. Sampling delays and guards are in fills.csv and decisions.csv. Sparse capture is unsuitable for submillisecond latency claims.

## Audit artifacts

- parents.csv: fills, unfilled quantity, implementation shortfall and additive cost components.
- fills.csv: every simulated child, observed depth price, timing, fees and five-second markout.
- decisions.csv: frozen-model forecast, requested quantity, label cutoff and guard outcomes.
- models.json: each fold's coefficients and training-only scaling parameters.
- forecasts.csv: start-of-episode forecasts and future labels, compared with zero change.
- manifest.json: configuration, input hash, source hashes, environment and artifact hashes.
