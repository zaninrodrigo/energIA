"""Ground-truth manifest: deterministic serialization of a `SyntheticDataset`'s `manifest` dict
(built in `generator.py`) to JSON bytes, plus disk I/O.

This is the fixture that later calibration work (docs/04-ai/AI_ENGINE_SPEC.md sections 6-9,
stages 3-6 of the Motor de Inteligencia Energética) reads: for every suministro, which categoria/
localidad/cold-start offset it got, and the full list of planted anomalies (which suministro,
which type, which period, which parameters) -- so a calibration run can check whether the engine
actually flagged what was planted.

`build_manifest()` must be a PURE function of `(seed, scale, months)`: `generator.py`'s own
determinism rule (a single seeded `random.Random(seed)`, no wall clock, no `numpy` global RNG
state) already guarantees this -- see that module's docstring. `serialize_manifest()` adds the
other half: a deterministic JSON *encoding* (sorted keys, fixed separators) so two runs of the
same `(seed, scale)` produce BYTE-IDENTICAL output, not just equal-as-dicts output.
"""

import json
from pathlib import Path

from energia.tools.synthetic.generator import generate_dataset


def build_manifest(seed: int, scale: str, *, months: int | None = None) -> dict[str, object]:
    """The ground-truth manifest dict alone, for a given `(seed, scale[, months])` -- a pure
    function of its inputs, derived by running the same deterministic pipeline
    `generate_dataset()` uses (this does not skip building the import payloads; there is no
    cheaper path today that still shares `generate_dataset()`'s single RNG stream honestly)."""
    return generate_dataset(seed, scale, months=months).manifest


def serialize_manifest(manifest: dict[str, object]) -> bytes:
    """Deterministic JSON encoding: sorted keys (so Python's dict insertion order can never leak
    into the bytes), a fixed 2-space indent, and a trailing newline (POSIX text-file convention).
    Every field in `generator.py`'s manifest dict is already a plain `int`/`str`/`bool`/`list`/
    `dict`/`float` -- `json.dumps` needs no custom encoder.
    """
    return (json.dumps(manifest, sort_keys=True, ensure_ascii=True, indent=2) + "\n").encode(
        "utf-8"
    )


def write_manifest(manifest: dict[str, object], path: Path) -> None:
    """Write `manifest` to `path` as deterministic JSON bytes (see `serialize_manifest()`),
    creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(serialize_manifest(manifest))
