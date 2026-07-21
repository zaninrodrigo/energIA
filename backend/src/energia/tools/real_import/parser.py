"""Parse the distributor's real consumption CSV into structured rows.

Format (REAL_DATA_IMPORT_SPEC.md §2): ISO-8859-1, ``;``-delimited, CRLF, consumption columns
``C_P{year}B{n}`` in reverse-chronological order. The pure `parse_meter_rows` (rows already read)
is what carries the mapping logic and is unit-tested; `read_csv_file` is the thin I/O wrapper.
"""

import csv
from dataclasses import dataclass
from pathlib import Path

from energia.tools.real_import.periods import Bimester, parse_consumo_column

_ENCODING = "latin-1"
_DELIMITER = ";"

# Column headers (REAL_DATA_IMPORT_SPEC.md §2.1).
_COL_SUMINISTRO = "SUMINISTRO"
_COL_MEDIDOR = "MEDIDOR"
_COL_TITULAR = "TITULAR"
_COL_CUIT = "CUIT"
_COL_LOCALIDAD = "LOCALIDAD_RUTA"
_COL_BARRIO = "BARRIO_RUTA"
_COL_LATITUD = "DMCLATITUD"
_COL_LONGITUD = "DMCLONGITUD"


@dataclass(frozen=True, slots=True)
class RealMeterRow:
    """One meter and its bimestral consumption history, as read from the CSV. `numero_suministro`
    is the ruta-folio (D1); `medidor` is the physical serial (D2). `consumos` maps each present
    bimester to its kWh."""

    numero_suministro: str
    medidor: str | None
    titular: str
    cuit: str
    localidad: str | None
    barrio: str | None
    latitud: float | None
    longitud: float | None
    consumos: dict[Bimester, float]


def clean_barrio(raw: str | None) -> str | None:
    """Strip the ``"B  "`` route prefix and surrounding whitespace (REAL_DATA_IMPORT_SPEC.md §4).
    Returns `None` for an empty/blank value."""
    if raw is None:
        return None
    value = raw.strip()
    if value.upper().startswith("B ") or value.upper().startswith("B\t"):
        value = value[1:].strip()
    return value or None


def _opt(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _opt_float(raw: str | None) -> float | None:
    value = _opt(raw)
    if value is None:
        return None
    return float(value.replace(",", "."))


def parse_meter_rows(header: list[str], data_rows: list[list[str]]) -> list[RealMeterRow]:
    """Turn already-read CSV rows into `RealMeterRow`s (pure -- no I/O). Consumption columns are
    detected by name (``C_P{year}B{n}``), so column order/count is irrelevant."""
    index = {name.strip(): i for i, name in enumerate(header)}
    consumo_columns = [
        (i, bimester)
        for i, name in enumerate(header)
        if (bimester := parse_consumo_column(name)) is not None
    ]

    def cell(row: list[str], name: str) -> str | None:
        i = index.get(name)
        return row[i] if i is not None and i < len(row) else None

    rows: list[RealMeterRow] = []
    for row in data_rows:
        consumos: dict[Bimester, float] = {}
        for i, bimester in consumo_columns:
            value = _opt_float(row[i]) if i < len(row) else None
            if value is not None:
                consumos[bimester] = value
        rows.append(
            RealMeterRow(
                numero_suministro=(cell(row, _COL_SUMINISTRO) or "").strip(),
                medidor=_opt(cell(row, _COL_MEDIDOR)),
                titular=(cell(row, _COL_TITULAR) or "").strip(),
                cuit=(cell(row, _COL_CUIT) or "").strip(),
                localidad=_opt(cell(row, _COL_LOCALIDAD)),
                barrio=clean_barrio(cell(row, _COL_BARRIO)),
                latitud=_opt_float(cell(row, _COL_LATITUD)),
                longitud=_opt_float(cell(row, _COL_LONGITUD)),
                consumos=consumos,
            )
        )
    return rows


def read_csv_file(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read the CSV (latin-1, ``;``) and return ``(header, data_rows)``."""
    with path.open(encoding=_ENCODING, newline="") as fh:
        all_rows = list(csv.reader(fh, delimiter=_DELIMITER))
    return all_rows[0], all_rows[1:]
