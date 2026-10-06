# Verification record

This record describes checks actually run for the delivered implementation. It does
not imply that the project has been tested on every supported Python version or
validated over a representative market sample.

## Environment

- macOS / Apple Silicon workspace.
- Python 3.14.6, NumPy 2.5.1 from the existing QuantTimeLearn virtual environment.
- Recorder also executed with the system `python3`; its public-data path uses the
  standard library and does not need NumPy.
- Python and NumPy versions are embedded in each research manifest.
- Fresh-environment editable installation instructions are supplied in the README;
  verification here used the existing local runtime.

## Tests executed

```bash
make PYTHON=../QuantTimeLearn/.venv/bin/python test
```

Result: **20 tests passed**. The suite runs against temporary output directories and
mocked network failures; it does not request live data or touch the saved capture.

The tests cover distinct research invariants:

| Area | Verified behavior |
|---|---|
| Book validation | Crossed, nonfinite, duplicate-price and zero-depth books rejected |
| Venue semantics | Aggregated level-2 sizes are not multiplied by order counts |
| Dataset integrity | Duplicate sequence, mixed product and mixed source rejected |
| Capture failure | Failed public requests preserve errors; no synthetic substitution |
| Liquidity | Sweeps stop at visible depth and retain unfilled quantity |
| Accounting | Independent buy/sell shortfall calculation matches the decomposition |
| Latency | Execution begins at the first eligible later observation |
| Data guards | Wide/stale books and unobservable execution gaps are recorded |
| Scheduling | AC endpoints and zero-urgency TWAP limit |
| Ablation | Zero adaptive strength reproduces TWAP |
| Price limits | The decision-time limit cannot chase a later price |
| Deadlines | Changing future receipt times leaves the first requested quantity unchanged |
| Model leakage | Future features/labels cannot change an earlier scaler or fitted model |
| Policy leakage | Future execution prices affect fills, not the earlier request |
| Label maturity | A model trained beyond the parent start is rejected |
| Pretrade | Large sizes disclose insufficient depth |
| Determinism | Identical fixture seed and config reproduce economic artifacts |
| Provenance | Tampered raw-capture hashes are rejected |
| Bootstrap | Small samples suppress intervals; fixed seed reproduces intervals |
| Output integrity | Saved artifact hashes match; existing output directories rejected |

## Public-data capture actually performed

Command:

```bash
python3 -m execution_lab capture --product BTC-USD \
  --samples 180 --interval 1 --depth 50 \
  --output data/captures/btc_verified_01
```

The source was the unauthenticated Coinbase Exchange level-2 book endpoint. The
capture manifest contains exact UTC start/end times and request settings.

- 180 requests attempted.
- 74 validated snapshots retained.
- 106 duplicate/regressed snapshot rejections recorded.
- Retained span: 179.796913 seconds.
- Median retained observation interval: 2.514428 seconds.
- Maximum retained observation interval: 3.036861 seconds.
- Normalized-data SHA-256:
  `48af6fb55c378f9616ffa278cb72de0ab50cf609ee33b259014590968aa1deac`.

The exchange capture required network access. Network access is unnecessary for
replaying the saved observations, running the demo, or running tests.

## Reference runs actually completed

The reference directories were new when these commands ran:

```bash
../QuantTimeLearn/.venv/bin/python -m execution_lab demo \
  --output results/final_demo --snapshots 1800

../QuantTimeLearn/.venv/bin/python -m execution_lab research \
  --input data/captures/btc_verified_01/snapshots.jsonl \
  --config config/smoke.toml --output results/final_live

../QuantTimeLearn/.venv/bin/python -m execution_lab stress \
  --input results/final_demo/synthetic_snapshots.jsonl \
  --output results/final_stress

../QuantTimeLearn/.venv/bin/python -m execution_lab quote \
  --input data/captures/btc_verified_01/snapshots.jsonl --sizes 0.01 0.1 1.0
```

All completed successfully. Use different output names when rerunning.

The synthetic experiment has 48 time windows, 96 side-specific parent cases and
four policies, totaling 384 parent-policy replays. The live check has seven time
windows, 14 side-specific cases and 56 parent-policy replays. The stress run repeats
the synthetic design for all five specified scenarios.

The final live data provenance is `local_capture_hashes_verified`. The source tag
alone would not be enough to establish that provenance.

## Findings to retain

The initial live replay exposed deadline misses caused by sparse valid observations.
Those development artifacts remain under `results/verified_live`. The final version
uses an arrival-time estimate of the observation cadence to reserve completion time.
This was an engineering change motivated by an observed failure, not a parameter
search for a favorable execution return. Longer unseen captures are required to
evaluate whether the reserve transfers reliably to other sessions.

After the change, the final live run completed every parent under every policy.
Adaptive minus TWAP mean cost was -0.0031507807601680276 bps. Only seven paired time
windows were available, so the program did not report a bootstrap confidence interval.
There is no supported claim of meaningful market execution improvement.

In synthetic stress, reducing available depth to 2% and increasing parent size 5x
produce equivalent normalized liquidity demands, so very similar outputs are expected.
They are two ways to expose the same capacity constraint, not independent confirmations.

A one-second synthetic sampling grid cannot resolve a 250 ms versus 1,000 ms arrival
when both map to the same next observed book. Small schedule differences can still
arise because the completion reserve includes latency. Such output must not be described
as a calibrated latency-impact curve.

## Remaining verification boundaries

- Real capture/replay is a short functional check; multiple-day performance is untested.
- Order entry, execution acknowledgments and production reconciliation are absent.
- Passive queue fills, own-order impact and counterfactual replenishment are unmodeled.
- Venue lot sizes, tick rounding and minimum-notional constraints are not enforced.
- REST polling does not give complete order events or subsecond execution fidelity.
- Fee tiers are configurable assumptions, not account-specific values.
- Floating-point arithmetic is appropriate for research; a trading adapter would
  require explicit tick/lot units and exact order validation.
- Integrity hashes detect changed local artifacts; they do not provide exchange-signed provenance.
