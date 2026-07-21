"""Bimester period math for the real-data import (REAL_DATA_IMPORT_SPEC.md, decision D4).

Real billing is bimestral: a column named ``C_P{year}B{n}`` (n = 1..6) is one bimester. Each
bimester becomes one processing lote so the Motor analyzes bimester by bimester, as in production.
Pure functions only -- unit-tested with hand-computed expectations, no I/O.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date

_CONSUMO_COLUMN = re.compile(r"^C_P(?P<year>\d{4})B(?P<bimester>[1-6])$")


@dataclass(frozen=True, slots=True)
class Bimester:
    """One bimester of a year. `n` is 1..6 (B1 = Jan-Feb, B2 = Mar-Apr, ... B6 = Nov-Dec)."""

    year: int
    n: int

    @property
    def codigo_lote(self) -> str:
        """Stable lote code for this bimester (REAL_DATA_IMPORT_SPEC.md §3)."""
        return f"REAL-{self.year}-B{self.n}"

    @property
    def fecha_inicio(self) -> date:
        return date(self.year, self.n * 2 - 1, 1)

    @property
    def fecha_fin(self) -> date:
        last_month = self.n * 2
        last_day = calendar.monthrange(self.year, last_month)[1]
        return date(self.year, last_month, last_day)

    @property
    def dias_facturados(self) -> int:
        return (self.fecha_fin - self.fecha_inicio).days + 1


def parse_consumo_column(name: str) -> Bimester | None:
    """Parse a ``C_P{year}B{n}`` column header into a `Bimester`, or `None` if it is not one
    (any other column -- identifiers, geography -- returns `None`)."""
    match = _CONSUMO_COLUMN.match(name.strip())
    if match is None:
        return None
    return Bimester(year=int(match.group("year")), n=int(match.group("bimester")))
