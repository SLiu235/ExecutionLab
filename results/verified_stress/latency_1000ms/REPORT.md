# ExecutionLab — experiment report

Data: **synthetic** · BTC-USD · 1800 validated snapshots · 1799.0 seconds observed.

48 non-overlapping parent time windows; independent buy and sell replay on each window. Same parent size, deadline, fee and visible-depth limits for every policy.

Parent: 0.1 base units; deadline: 30s; assumed fee: 5.0 bps; latency floor: 1000ms; available depth haircut: 10%.

| Policy | Completion | Average filled | Mean marked cost (bps) | P95 cost (bps) |
|---|---:|---:|---:|---:|
| immediate | 100.0% | 100.0% | 5.4514 | 6.2653 |
| twap | 100.0% | 100.0% | 5.1974 | 8.0410 |
| ac_schedule | 100.0% | 100.0% | 5.1969 | 7.4991 |
| adaptive | 100.0% | 100.0% | 5.1905 | 8.0317 |

Lower cost is better. Marked cost includes terminal-mid opportunity cost on unfilled quantity; it is not a forced-completion price. Completion must be assessed alongside cost.

Adaptive minus TWAP, on fully completed paired time blocks: **-0.0069 bps**. Included blocks: 48; excluded for noncompletion: 0.

Paired 95% interval: [-0.009690000485332478, -0.004983475888029057]. exploratory paired moving-block bootstrap; one session is not market validation.

Frozen ridge forecast RMSE: 0.6357 bps; zero-change baseline: 0.6974 bps over 48 future-return labels.

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
