from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Public order-book capture and execution research")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture", help="record public Coinbase level-2 snapshots")
    capture.add_argument("--product", default="BTC-USD")
    capture.add_argument("--samples", type=int, default=600)
    capture.add_argument("--interval", type=float, default=1.0)
    capture.add_argument("--depth", type=int, default=50)
    capture.add_argument("--output", type=Path, required=True)
    quote = sub.add_parser("quote", help="estimate as-of-snapshot costs for proposed sizes")
    quote.add_argument("--input", type=Path, required=True)
    quote.add_argument("--side", choices=["buy", "sell"], default="buy")
    quote.add_argument("--sizes", type=float, nargs="+", default=[.01, .1, 1.0])
    quote.add_argument("--fee-bps", type=float, default=5.0)
    quote.add_argument("--depth-fraction", type=float, default=.10)
    for name in ("demo", "research", "stress"):
        p = sub.add_parser(name)
        p.add_argument("--output", type=Path, required=True)
        p.add_argument("--config", type=Path)
        if name == "demo":
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--snapshots", type=int, default=1800)
        else:
            p.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "quote":
        from .data import load_books
        from .pretrade import quote as estimate
        print(json.dumps(estimate(load_books(args.input)[-1], args.side, args.sizes,
                                  args.fee_bps, args.depth_fraction), indent=2, allow_nan=False))
        return
    if args.command == "capture":
        from .data import capture as record
        result = record(args.output, args.product, args.samples, args.interval, args.depth)
        print(json.dumps({"accepted": result["accepted"], "status": result["status"],
                          "path": str(args.output)}, indent=2))
        return
    from .research import Config, run, stress
    cfg = Config.from_file(args.config) if args.config else Config()
    if args.command == "demo":
        from .synthetic import generate
        from .data import new_directory
        new_directory(args.output)
        path = args.output / "synthetic_snapshots.jsonl"
        generate(path, args.snapshots, args.seed)
        run(path, args.output / "experiment", cfg)
    elif args.command == "research":
        run(args.input, args.output, cfg)
    else:
        stress(args.input, args.output, cfg)
    print(f"Results: {args.output.resolve()}")
