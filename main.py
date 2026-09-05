"""Single entrypoint: python main.py runs the full synthetic batch end to end.

Supports configurable batch size and random seed:
  python main.py
  python main.py --size 500 --seed 42
"""
import argparse
import sys
from benchmark import run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Vaapas: Hinglish Payment & Mandate Recovery Agent")
    parser.add_argument(
        "--size",
        type=int,
        default=55,
        help="Number of synthetic records to generate and evaluate (default: 55)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic random seed for reproducibility (default: 42)",
    )
    parser.add_argument(
        "--log",
        type=str,
        default="audit_log.jsonl",
        help="Path to output JSONL audit log (default: audit_log.jsonl)",
    )
    args = parser.parse_args()

    run_benchmark(n=args.size, seed=args.seed, log_path=args.log)


if __name__ == "__main__":
    main()
