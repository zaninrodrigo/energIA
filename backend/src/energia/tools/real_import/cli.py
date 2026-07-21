"""CLI for the real-data import: read the distributor's CSV and load it through the API.

    python -m energia.tools.real_import --file "<ruta.csv>" --base-url http://localhost:8000

Requires the API running. Reads the file locally and never writes it anywhere -- the source CSV
holds real personal data (REAL_DATA_IMPORT_SPEC.md privacy note).
"""

import argparse
import asyncio
from pathlib import Path

import httpx

from energia.tools.real_import.loader import ImportReport, import_payloads
from energia.tools.real_import.mapper import build_payloads
from energia.tools.real_import.parser import parse_meter_rows, read_csv_file


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Importar el CSV real de consumos a EnergIA.")
    parser.add_argument("--file", required=True, help="Ruta al CSV de históricos de consumo.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="URL base de la API.")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser.parse_args(argv)


def _print_report(report: ImportReport, payloads_counts: dict[str, int]) -> None:
    print("\n=== Importación de datos reales ===")
    for entity, result in report.por_entidad.items():
        print(
            f"  {entity:<12} enviados={payloads_counts[entity]:>5} "
            f"creados={result.created:>5} actualizados={result.updated:>5} "
            f"sin_cambio={result.unchanged:>5} rechazados={len(result.rejected):>4}"
        )
        for rejected in result.rejected[:5]:
            print(f"      rechazo: {rejected.get('reasons')}")


async def _run(args: argparse.Namespace) -> None:
    header, data_rows = read_csv_file(Path(args.file))
    rows = parse_meter_rows(header, data_rows)
    payloads = build_payloads(rows)
    counts = {
        "clientes": len(payloads.clientes),
        "suministros": len(payloads.suministros),
        "lotes": len(payloads.lotes),
        "consumos": len(payloads.consumos),
    }
    print(f"Filas leídas: {len(rows)} | payloads: {counts}")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout) as client:
        report = await import_payloads(client, payloads, batch_size=args.batch_size)
    _print_report(report, counts)


def main(argv: list[str] | None = None) -> None:
    asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":  # pragma: no cover
    main()
