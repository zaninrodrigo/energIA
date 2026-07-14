"""Integration-style test for the thin CLI wiring (argument parsing + orchestration glue).

`api_loader.load_dataset` is monkeypatched to a fake (no network, no DB) -- the CLI's own logic
under test here is argument parsing, output-path resolution, manifest writing, and exit-code /
error-message wiring, not `load_dataset`'s own behavior (already covered by
`tests/integration/tools/synthetic/test_api_loader_integration.py`).
"""

import json
from pathlib import Path

import pytest

from energia.tools.synthetic import cli
from energia.tools.synthetic.api_loader import ImportRejectionError, LoadReport
from energia.tools.synthetic.generator import SyntheticDataset


async def _fake_load_dataset(
    client: object, dataset: SyntheticDataset, *, batch_size: int
) -> LoadReport:
    del client, batch_size
    counts = {
        "clientes": len(dataset.clientes),
        "suministros": len(dataset.suministros),
        "lotes": len(dataset.lotes),
        "lecturas": len(dataset.lecturas),
        "consumos": len(dataset.consumos),
    }
    return LoadReport(
        created=dict(counts),
        updated=dict.fromkeys(counts, 0),
        unchanged=dict.fromkeys(counts, 0),
        restored=dict.fromkeys(counts, 0),
    )


def test_main_generates_writes_the_manifest_and_loads_the_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_dataset", _fake_load_dataset)

    exit_code = cli.main(
        [
            "--base-url",
            "http://example.invalid",
            "--scale",
            "small",
            "--seed",
            "7",
            "--out",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    manifest_path = tmp_path / "small-seed7" / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_bytes())
    assert manifest["seed"] == 7
    assert manifest["scale"] == "small"
    captured = capsys.readouterr()
    assert "clientes" in captured.out
    assert "consumos" in captured.out


def test_years_flag_overrides_the_scale_default_month_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "load_dataset", _fake_load_dataset)

    cli.main(
        [
            "--scale",
            "small",  # small's own default is 24 months (2 years)
            "--years",
            "3",
            "--out",
            str(tmp_path),
        ]
    )

    manifest = json.loads((tmp_path / "small-seed42" / "manifest.json").read_bytes())
    assert manifest["months"] == 36


def test_main_returns_1_and_prints_to_stderr_on_an_import_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def _rejecting_load_dataset(
        client: object, dataset: SyntheticDataset, *, batch_size: int
    ) -> LoadReport:
        del client, dataset, batch_size
        raise ImportRejectionError("/api/v1/clientes/import: 1 record(s) rejected")

    monkeypatch.setattr(cli, "load_dataset", _rejecting_load_dataset)

    exit_code = cli.main(["--scale", "small", "--out", str(tmp_path)])

    assert exit_code == 1
    assert "rejected" in capsys.readouterr().err
