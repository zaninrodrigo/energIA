"""Unit tests for energia.tools.synthetic.manifest.

The manifest must be a PURE function of (seed, scale): same inputs -> byte-identical JSON, every
time, with no wall-clock timestamp, no dict-ordering nondeterminism, no hash-randomization
leakage.
"""

import json

from energia.tools.synthetic.manifest import build_manifest, serialize_manifest, write_manifest


def test_build_manifest_is_a_pure_function_of_seed_and_scale() -> None:
    first = build_manifest(seed=42, scale="small")
    second = build_manifest(seed=42, scale="small")

    assert serialize_manifest(first) == serialize_manifest(second)


def test_build_manifest_differs_for_a_different_seed() -> None:
    first = build_manifest(seed=1, scale="small")
    second = build_manifest(seed=2, scale="small")

    assert serialize_manifest(first) != serialize_manifest(second)


def test_manifest_contains_seed_scale_suministros_and_anomalias() -> None:
    manifest = build_manifest(seed=42, scale="small")

    assert manifest["seed"] == 42
    assert manifest["scale"] == "small"
    assert len(manifest["suministros"]) == 100  # type: ignore[arg-type]
    assert len(manifest["anomalias"]) >= 4  # at least one of each of the 4 injector types


def test_serialize_manifest_produces_valid_sorted_json_with_a_trailing_newline() -> None:
    manifest = build_manifest(seed=42, scale="small")

    raw = serialize_manifest(manifest)

    assert raw.endswith(b"\n")
    assert json.loads(raw) == manifest


def test_write_manifest_writes_the_same_bytes_serialize_manifest_would(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    manifest = build_manifest(seed=42, scale="small")
    destination = tmp_path / "manifest.json"

    write_manifest(manifest, destination)

    assert destination.read_bytes() == serialize_manifest(manifest)
