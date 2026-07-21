from datetime import date

from energia.tools.real_import.periods import Bimester, parse_consumo_column


def test_bimester_bounds_and_lote_code() -> None:
    b4 = Bimester(year=2026, n=4)  # Jul-Aug
    assert b4.codigo_lote == "REAL-2026-B4"
    assert b4.fecha_inicio == date(2026, 7, 1)
    assert b4.fecha_fin == date(2026, 8, 31)
    assert b4.dias_facturados == 62


def test_february_leap_year_bimester() -> None:
    b1_2024 = Bimester(year=2024, n=1)  # Jan-Feb 2024 (leap)
    assert b1_2024.fecha_inicio == date(2024, 1, 1)
    assert b1_2024.fecha_fin == date(2024, 2, 29)
    assert b1_2024.dias_facturados == 60


def test_parse_consumo_column_recognizes_only_cp_columns() -> None:
    assert parse_consumo_column("C_P2025B6") == Bimester(2025, 6)
    assert parse_consumo_column(" C_P2023B1 ") == Bimester(2023, 1)
    assert parse_consumo_column("SUMINISTRO") is None
    assert parse_consumo_column("C_P2025B7") is None  # only B1..B6 exist
