from energia.tools.real_import.parser import clean_barrio, parse_meter_rows
from energia.tools.real_import.periods import Bimester

_HEADER = [
    "   ",
    "LD_DISTRITO",
    "SUMINISTRO",
    "MEDIDOR",
    "TITULAR",
    "CUIT",
    "LOCALIDAD_RUTA",
    "BARRIO_RUTA",
    "DMCLATITUD",
    "DMCLONGITUD",
    "C_P2024B2",
    "C_P2024B1",
]


def test_clean_barrio_strips_route_prefix_and_whitespace() -> None:
    assert clean_barrio("B  San Martin") == "San Martin"
    assert clean_barrio("  B  Colluccio ") == "Colluccio"
    assert clean_barrio("") is None
    assert clean_barrio(None) is None


def test_parse_meter_rows_maps_fields_and_bimestral_consumos() -> None:
    row = [
        "19",
        "00 - FORMOSA",
        "00201002902",
        "334604",
        "BRITO EVELYN MARIEL ",
        "27399009621",
        "FORMOSA",
        "B  San Martin",
        "-26.1848169",
        "-58.1953032",
        "2700",
        "1086",
    ]
    [meter] = parse_meter_rows(_HEADER, [row])

    assert meter.numero_suministro == "00201002902"
    assert meter.medidor == "334604"
    assert meter.titular == "BRITO EVELYN MARIEL"
    assert meter.cuit == "27399009621"
    assert meter.localidad == "FORMOSA"
    assert meter.barrio == "San Martin"
    assert meter.latitud == -26.1848169
    assert meter.longitud == -58.1953032
    assert meter.consumos == {Bimester(2024, 2): 2700.0, Bimester(2024, 1): 1086.0}


def test_blank_consumo_cells_are_skipped_not_zero() -> None:
    row = ["1", "d", "S1", "", "T", "20", "FORMOSA", "B  Centro", "", "", "500", ""]
    [meter] = parse_meter_rows(_HEADER, [row])

    assert meter.medidor is None
    assert meter.latitud is None
    assert meter.consumos == {Bimester(2024, 2): 500.0}  # the empty B1 cell dropped, not 0
