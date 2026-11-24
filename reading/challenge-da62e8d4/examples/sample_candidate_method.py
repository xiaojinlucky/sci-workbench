#!/usr/bin/env python3
"""ILLUSTRATIVE SYNTHETIC EXAMPLE ONLY — NOT A FORMAL BENCHMARK SCRIPT."""

from __future__ import annotations

import argparse
import json
import random


def main() -> int:
    parser = argparse.ArgumentParser(description="Synthetic candidate numerical method example.")
    parser.add_argument("--learning-rate", type=float, default=0.01)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()

    random.seed(args.seed)
    loss = 1.0
    for _ in range(args.iterations):
        loss = loss * (1.0 - args.learning_rate * 1.7)
    accuracy = min(0.99, 1.0 - 0.45 * loss)
    print(json.dumps({"accuracy": round(accuracy, 6), "loss": round(loss, 6)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
