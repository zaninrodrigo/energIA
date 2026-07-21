from energia.tools.real_import.mapper import (
    CATEGORIA_TARIFARIA_DEFAULT,
    build_payloads,
)
from energia.tools.real_import.parser import RealMeterRow
from energia.tools.real_import.periods import Bimester


def _row(numero: str, cuit: str, titular: str, consumos: dict[Bimester, float]) -> RealMeterRow:
    return RealMeterRow(
        numero_suministro=numero,
        medidor="M-" + numero,
        titular=titular,
        cuit=cuit,
        localidad="FORMOSA",
        barrio="Centro",
        latitud=-26.18,
        longitud=-58.17,
        consumos=consumos,
    )


def test_build_payloads_shapes_all_four_entities() -> None:
    rows = [
        _row("S1", "20111111119", "ALICE", {Bimester(2024, 1): 100.0, Bimester(2024, 2): 200.0}),
        _row("S2", "20111111119", "ALICE", {Bimester(2024, 1): 50.0}),  # same CUIT -> one cliente
    ]

    p = build_payloads(rows)

    # One cliente per CUIT (both rows share it), two suministros, two bimester lotes, 3 consumos.
    assert len(p.clientes) == 1
    assert p.clientes[0]["numero_cliente"] == "20111111119"
    assert len(p.suministros) == 2
    assert p.suministros[0]["categoria_tarifaria"] == CATEGORIA_TARIFARIA_DEFAULT
    assert p.suministros[0]["medidor"] == "M-S1"
    assert p.suministros[0]["fecha_alta"] == "2024-01-01"  # earliest bimester's start
    assert {lote["codigo_lote"] for lote in p.lotes} == {"REAL-2024-B1", "REAL-2024-B2"}
    assert len(p.consumos) == 3


def test_lote_cantidad_registros_counts_its_consumos() -> None:
    rows = [
        _row("S1", "20111111119", "A", {Bimester(2025, 3): 10.0}),
        _row("S2", "20222222227", "B", {Bimester(2025, 3): 20.0}),
    ]

    p = build_payloads(rows)

    [lote] = p.lotes
    assert lote["codigo_lote"] == "REAL-2025-B3"
    assert lote["cantidad_registros"] == 2


def test_row_without_cuit_falls_back_to_suministro_keyed_cliente() -> None:
    rows = [_row("S9", "", "SIN CUIT", {Bimester(2024, 1): 10.0})]

    p = build_payloads(rows)

    assert p.clientes[0]["numero_cliente"] == "S-S9"
    assert p.suministros[0]["numero_cliente"] == "S-S9"
