"""Thin CLI entrypoint: parses arguments, builds the deterministic dataset, writes the
ground-truth manifest, and loads the dataset through a running EnergIA API instance.

Usage: `python -m energia.tools.synthetic --base-url http://localhost:8000 --scale small
--seed 42 --out datasets/synthetic/` (also wired as `make seed-synthetic`, `backend/Makefile`).

No domain logic lives here -- `generator.py` builds the dataset, `manifest.py` writes the
ground truth, `api_loader.py` does the actual importing. This module only parses arguments,
resolves the output path, and reports the outcome (exit code 0 on success, 1 -- with the
rejection detail on stderr -- if `api_loader` ever raises `ImportRejectionError`).
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from energia.tools.synthetic.api_loader import ImportRejectionError, LoadReport, load_dataset
from energia.tools.synthetic.generator import SCALES, generate_dataset
from energia.tools.synthetic.manifest import write_manifest

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_SCALE = "small"
DEFAULT_SEED = 42
DEFAULT_BATCH_SIZE = 500
DEFAULT_OUT_DIR = Path("datasets/synthetic")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m energia.tools.synthetic",
        description=(
            "Populate an EnergIA instance with a deterministic synthetic dataset, through its "
            "real import API, recording every planted anomaly in a ground-truth manifest for "
            "calibrating the Motor de Inteligencia Energetica (stages 3-6)."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL of a running EnergIA API instance (default: %(default)s).",
    )
    parser.add_argument(
        "--scale",
        choices=sorted(SCALES),
        default=DEFAULT_SCALE,
        help="Dataset size: small=100 suministros/24 months, medium=1000/36, large=5000/36 "
        "(default: %(default)s).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="RNG seed -- same seed and scale always produce the same dataset and manifest "
        "(default: %(default)s).",
    )
    parser.add_argument(
        "--years",
        type=int,
        choices=(2, 3),
        default=None,
        help="Years of monthly history to generate; overrides --scale's own default month "
        "count when given (default: unset, uses --scale's default).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Base output directory; the run's manifest.json is written under "
        "<out>/<scale>-seed<seed>/ (default: %(default)s).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Max records per import POST (default: %(default)s).",
    )
    return parser.parse_args(argv)


def _run_output_dir(out_base: Path, scale: str, seed: int) -> Path:
    """Deterministic run directory name (no wall-clock timestamp): `<scale>-seed<seed>`, so
    re-running the same `(scale, seed)` always writes to the same place."""
    return out_base / f"{scale}-seed{seed}"


def _print_report(report: LoadReport, *, scale: str, seed: int) -> None:
    print(f"Loaded synthetic dataset (scale={scale}, seed={seed}):")
    for entity, created in report.created.items():
        print(
            f"  {entity}: created={created} updated={report.updated[entity]} "
            f"unchanged={report.unchanged[entity]} restored={report.restored[entity]}"
        )


async def _async_main(args: argparse.Namespace) -> LoadReport:
    months = args.years * 12 if args.years is not None else None
    dataset = generate_dataset(args.seed, args.scale, months=months)

    run_dir = _run_output_dir(args.out, args.scale, args.seed)
    write_manifest(dataset.manifest, run_dir / "manifest.json")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30.0) as client:
        return await load_dataset(client, dataset, batch_size=args.batch_size)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        report = asyncio.run(_async_main(args))
    except ImportRejectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    _print_report(report, scale=args.scale, seed=args.seed)
    return 0
