"""Adversarial tests for research validity and economic accounting."""
from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from execution_lab.data import Book, capture, load_books, normalize, sha256
from execution_lab.features import matrix
from execution_lab.model import RidgeForecast, fit_past
from execution_lab.pretrade import quote
from execution_lab.research import Config, run, paired_interval
from execution_lab.simulator import ExecutionConfig, cumulative_fraction, replay, sweep
from execution_lab.synthetic import generate

EPOCH = 1_700_000_000_000_000_000


def book(i=0, mid=100., depth=1.):
    return Book("BTC-USD", EPOCH+i*1_000_000_000+20_000_000, EPOCH+i*1_000_000_000,
                i, ((mid-1, depth), (mid-2, depth)), ((mid+1, depth), (mid+2, depth)), "synthetic")


def frozen():
    return RidgeForecast(np.zeros(8), np.ones(8), np.zeros(8), 0., 0, 20)


class DataTests(unittest.TestCase):
    def test_reject_crossed_nonfinite_duplicate_prices_and_zero_depth(self):
        for changes in ({"asks": ((98., 1.),)}, {"bids": ((float("nan"), 1.),)},
                        {"asks": ((101., 1.), (101., 2.))}, {"asks": ((101., 0.),)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                dataclasses.replace(book(), **changes)

    def test_level2_quantity_is_not_multiplied_by_order_count(self):
        p = {"time": "2023-11-14T22:13:20Z", "sequence": 1,
             "bids": [["99", "2", 7]], "asks": [["101", "3", 8]]}
        b = normalize(p, "BTC-USD", EPOCH+20_000_000, 5)
        self.assertEqual(b.bids[0][1], 2.)
        self.assertEqual(b.asks[0][1], 3.)
        with self.assertRaises(ValueError):
            normalize({**p, "auction_mode": True}, "BTC-USD", EPOCH, 5)

    def test_reject_duplicate_sequence_and_mixed_provenance(self):
        for second in (dataclasses.replace(book(1), sequence=0),
                       dataclasses.replace(book(1), source="coinbase_rest"),
                       dataclasses.replace(book(1), product="ETH-USD")):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d)/"data.jsonl"
                p.write_text("\n".join(json.dumps(b.row()) for b in (book(), second)))
                with self.assertRaises(ValueError):
                    load_books(p)

    def test_network_failure_keeps_error_manifest_without_synthetic_fallback(self):
        with tempfile.TemporaryDirectory() as d, patch("execution_lab.data.urlopen", side_effect=OSError("offline")), \
                patch("execution_lab.data.time.sleep"):
            output = Path(d)/"capture"
            with self.assertRaises(RuntimeError):
                capture(output, samples=2)
            m = json.loads((output/"manifest.json").read_text())
            self.assertEqual(m["status"], "failed")
            self.assertEqual(len(m["errors"]), 2)
            self.assertEqual((output/"snapshots.jsonl").read_text(), "")
            self.assertEqual(m["hashes"]["raw.jsonl"], sha256(output/"raw.jsonl"))


class AccountingTests(unittest.TestCase):
    def test_sweep_partial_and_both_sides(self):
        for side, expected in (("buy", 202.), ("sell", 198.)):
            q, cash = sweep(book(depth=2), side, 10., .5)
            self.assertEqual(q, 2.)
            # One unit at each level, rather than unlimited size at the touch.
            self.assertEqual(cash, expected+1 if side == "buy" else expected-1)

    def test_shortfall_decomposition_and_fee_for_buy_and_sell(self):
        books = [book(i, mid=100+i) for i in range(8)]
        cfg = ExecutionConfig(quantity=.5, fee_bps=10, depth_fraction=1., max_spread_bps=300,
                              max_slippage_bps=1000)
        for side in ("buy", "sell"):
            s, fills, _ = replay(books, matrix(books), frozen(), 1, 5, side, "immediate", cfg)
            sign = 1 if side == "buy" else -1
            f = fills[0]
            expected = (sign*(f["price"]-101) + f["price"]*.001)/101*1e4
            self.assertAlmostEqual(s["realized_shortfall_bps"], expected)
            self.assertAlmostEqual(s["realized_shortfall_bps"],
                                   sum(s[k] for k in ("delay_bps", "spread_bps", "depth_bps", "fee_bps")))
            self.assertEqual(f["execution_index"], 2)
            self.assertTrue(s["completed"])

    def test_partial_fills_never_invent_completion(self):
        books = [book(i, mid=100+i, depth=.01) for i in range(8)]
        cfg = ExecutionConfig(quantity=1, fee_bps=0, depth_fraction=.1, max_spread_bps=300,
                              max_slippage_bps=1000)
        s, fills, _ = replay(books, matrix(books), frozen(), 1, 5, "buy", "immediate", cfg)
        self.assertFalse(s["completed"])
        self.assertAlmostEqual(s["filled_quantity"], .008)
        self.assertAlmostEqual(s["opportunity_bps"], (1-.008)*(105-101)/101*1e4)
        self.assertEqual(len({f["execution_index"] for f in fills}), len(fills))

    def test_latency_floor_and_data_gap_guard(self):
        books = [book(i) for i in range(10)]
        cfg = ExecutionConfig(quantity=.1, latency_ms=1500, max_spread_bps=300,
                              max_slippage_bps=1000)
        _, fills, _ = replay(books, matrix(books), frozen(), 1, 8, "buy", "immediate", cfg)
        self.assertEqual(fills[0]["execution_index"], 3)
        cfg = dataclasses.replace(cfg, latency_ms=0, max_gap_seconds=.1)
        s, fills, _ = replay(books, matrix(books), frozen(), 1, 8, "buy", "immediate", cfg)
        self.assertFalse(fills)
        self.assertEqual(s["fill_rate"], 0.)
        self.assertGreater(s["guard_rejections"], 0)

    def test_spread_and_stale_guards(self):
        books = [book(i) for i in range(8)]
        cfg = ExecutionConfig(max_spread_bps=1.)
        s, fills, _ = replay(books, matrix(books), frozen(), 1, 5, "buy", "twap", cfg)
        self.assertFalse(fills)
        self.assertGreater(s["guard_rejections"], 0)
        old = [dataclasses.replace(b, exchange_ns=b.exchange_ns-20_000_000_000) for b in books]
        s, fills, _ = replay(old, matrix(old), frozen(), 1, 5, "sell", "twap",
                             dataclasses.replace(cfg, max_spread_bps=300))
        self.assertFalse(fills)

    def test_schedule_endpoints_and_zero_urgency_limit(self):
        for f in np.linspace(0, 1, 21):
            self.assertAlmostEqual(cumulative_fraction("ac_schedule", f, 0), f)
            self.assertGreaterEqual(cumulative_fraction("ac_schedule", f, 2), f-1e-12)
        self.assertEqual(cumulative_fraction("ac_schedule", 0, 2), 0.)
        self.assertEqual(cumulative_fraction("ac_schedule", 1, 2), 1.)

    def test_zero_alpha_adaptive_matches_twap(self):
        books = [book(i) for i in range(40)]
        cfg = ExecutionConfig(quantity=.1, max_spread_bps=300, adaptive_strength=0,
                              max_slippage_bps=1000)
        X = matrix(books)
        a, _, _ = replay(books, X, frozen(), 1, 30, "buy", "adaptive", cfg)
        t, _, _ = replay(books, X, frozen(), 1, 30, "buy", "twap", cfg)
        a.pop("policy"), t.pop("policy")
        self.assertEqual(a, t)

    def test_pretrade_sizes_do_not_claim_infinite_liquidity(self):
        r = quote(book(), "buy", [.1, 100], 5, .1)
        self.assertEqual(r["quotes"][0]["unfilled_quantity"], 0)
        self.assertGreater(r["quotes"][1]["unfilled_quantity"], 99)
        self.assertGreater(r["quotes"][0]["one_way_cost_bps_on_filled"], 5)

    def test_limit_is_set_at_decision_and_cannot_chase_future_prices(self):
        books = [book(i, mid=100. if i < 2 else 120.) for i in range(8)]
        cfg = ExecutionConfig(max_spread_bps=300, max_slippage_bps=150.)
        _, fills, decisions = replay(books, matrix(books), frozen(), 1, 5,
                                     "buy", "immediate", cfg)
        self.assertAlmostEqual(decisions[0]["limit_price"], 101.5)
        self.assertEqual(decisions[0]["status"], "limit_not_marketable")
        self.assertGreater(fills[0]["decision_index"], 1)

    def test_deadline_is_predeclared_not_derived_from_future_capture(self):
        books = [book(i) for i in range(40)]
        changed = books[:2]+[dataclasses.replace(b, received_ns=b.received_ns+100_000_000)
                             for b in books[2:]]
        cfg = ExecutionConfig(max_spread_bps=300, max_slippage_bps=1000)
        _, _, a = replay(books, matrix(books), frozen(), 1, 29, "buy", "twap", cfg)
        _, _, b = replay(changed, matrix(changed), frozen(), 1, 29, "buy", "twap", cfg)
        self.assertEqual(a[0]["requested_quantity"], b[0]["requested_quantity"])


class TemporalTests(unittest.TestCase):
    def test_future_mutation_cannot_change_scaler_model_or_prior_features(self):
        rng = np.random.default_rng(4)
        X = rng.normal(size=(160, 8))
        mids = 100+np.cumsum(rng.normal(0, .01, 160))
        m1 = fit_past(X, mids, 100, 3, 60, embargo=2)
        X2, mids2 = X.copy(), mids.copy()
        X2[98:] += 1000
        mids2[98:] += 500
        m2 = fit_past(X2, mids2, 100, 3, 60, embargo=2)
        self.assertEqual(m1.record(), m2.record())
        self.assertLess(m1.label_end_max, 98)
        books = [book(i) for i in range(130)]
        changed = books[:100]+[book(i, mid=500+i) for i in range(100, 130)]
        np.testing.assert_array_equal(matrix(books)[:100], matrix(changed)[:100])

    def test_future_execution_price_cannot_change_first_decision(self):
        books = [book(i) for i in range(40)]
        changed = books[:2]+[book(i, mid=200+i) for i in range(2, 40)]
        cfg = ExecutionConfig(max_spread_bps=300, max_slippage_bps=20000)
        _, f1, d1 = replay(books, matrix(books), frozen(), 1, 30, "buy", "adaptive", cfg)
        _, f2, d2 = replay(changed, matrix(changed), frozen(), 1, 30, "buy", "adaptive", cfg)
        self.assertEqual(d1[0], d2[0])
        self.assertNotEqual(f1[0]["price"], f2[0]["price"])

    def test_future_label_model_is_rejected(self):
        books = [book(i) for i in range(10)]
        m = dataclasses.replace(frozen(), label_end_max=1)
        with self.assertRaises(ValueError):
            replay(books, matrix(books), m, 1, 8, "buy", "twap", ExecutionConfig())


class WorkflowTests(unittest.TestCase):
    def test_tampered_capture_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            books = [dataclasses.replace(book(i), source="coinbase_rest") for i in range(60)]
            (p/"snapshots.jsonl").write_text("\n".join(json.dumps(b.row()) for b in books))
            (p/"raw.jsonl").write_text("tampered evidence\n")
            (p/"manifest.json").write_text(json.dumps({"source": "coinbase_rest", "hashes": {
                "snapshots.jsonl": sha256(p/"snapshots.jsonl"), "raw.jsonl": "invalid"}}))
            with self.assertRaisesRegex(ValueError, "evidence/hash mismatch"):
                run(p/"snapshots.jsonl", p/"out", Config(warmup_snapshots=28, train_window=24))

    def test_reproducible_complete_pipeline_and_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            generate(p/"fixture.jsonl", 200, 7)
            generate(p/"same.jsonl", 200, 7)
            self.assertEqual(sha256(p/"fixture.jsonl"), sha256(p/"same.jsonl"))
            cfg = Config(warmup_snapshots=28, train_window=24, episode_seconds=15,
                         bootstrap_repetitions=100)
            result = run(p/"fixture.jsonl", p/"a", cfg)
            repeated = run(p/"fixture.jsonl", p/"b", cfg)
            self.assertEqual(result, repeated)
            for name in ("parents.csv", "fills.csv", "decisions.csv", "models.json"):
                self.assertEqual(sha256(p/"a"/name), sha256(p/"b"/name))
            manifest = json.loads((p/"a"/"manifest.json").read_text())
            for name, digest in manifest["artifact_hashes"].items():
                self.assertEqual(digest, sha256(p/"a"/name))
            with self.assertRaises(FileExistsError):
                run(p/"fixture.jsonl", p/"a", cfg)

    def test_bootstrap_respects_small_sample_and_seed(self):
        cfg = Config(bootstrap_repetitions=100)
        self.assertIsNone(paired_interval([1, 2], cfg)["ci95_bps"])
        a = paired_interval(list(range(20)), cfg)
        self.assertEqual(a, paired_interval(list(range(20)), cfg))
        self.assertLess(a["ci95_bps"][0], a["mean_delta_bps"])
        self.assertGreater(a["ci95_bps"][1], a["mean_delta_bps"])


if __name__ == "__main__":
    unittest.main()
