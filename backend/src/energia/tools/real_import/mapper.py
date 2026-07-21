"""Map parsed `RealMeterRow`s to the import API's payloads (REAL_DATA_IMPORT_SPEC.md §2.1/§3).

Pure -- no I/O, no HTTP. Produces the four payload lists in FK-dependency order (clientes ->
suministros -> lotes -> consumos), which is the order the loader must POST them.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from energia.tools.real_import.parser import RealMeterRow

# The CSV carries no tariff category; v1 defaults every meter to the seeded "Residencial"
# (04_seed.sql), the dominant segment. Documented in REAL_DATA_IMPORT_SPEC.md as an open point.
CATEGORIA_TARIFARIA_DEFAULT = "Residencial"


@dataclass(frozen=True, slots=True)
class ImportPayloads:
    """The four import payload lists, in the order the loader must POST them."""

    clientes: list[dict[str, Any]] = field(default_factory=list)
    suministros: list[dict[str, Any]] = field(default_factory=list)
    lotes: list[dict[str, Any]] = field(default_factory=list)
    consumos: list[dict[str, Any]] = field(default_factory=list)


def _numero_cliente(row: RealMeterRow) -> str:
    """Use the CUIT as the client's natural key; fall back to the ruta-folio when a row has no
    CUIT (so the suministro still gets a client rather than being rejected)."""
    return row.cuit if row.cuit else f"S-{row.numero_suministro}"


def build_payloads(rows: list[RealMeterRow]) -> ImportPayloads:
    clientes: dict[str, dict[str, Any]] = {}
    suministros: list[dict[str, Any]] = []
    consumos: list[dict[str, Any]] = []
    lote_counts: Counter[str] = Counter()
    lote_nombres: dict[str, str] = {}

    for row in rows:
        numero_cliente = _numero_cliente(row)
        # One cliente per natural key: rows sharing a CUIT (a titular with several suministros)
        # collapse into a single cliente. Later rows keep the first-seen name.
        clientes.setdefault(
            numero_cliente,
            {
                "numero_cliente": numero_cliente,
                "nombre": row.titular,
                "documento": row.cuit or None,
            },
        )

        # fecha_alta: the start of the earliest bimester this meter has data for (a sensible proxy
        # -- the CSV carries no alta date). Skip meters with no consumption at all.
        if not row.consumos:
            continue
        earliest = min(row.consumos, key=lambda b: (b.year, b.n))

        suministros.append(
            {
                "numero_suministro": row.numero_suministro,
                "numero_cliente": numero_cliente,
                "categoria_tarifaria": CATEGORIA_TARIFARIA_DEFAULT,
                "fecha_alta": earliest.fecha_inicio.isoformat(),
                "localidad": row.localidad,
                "barrio": row.barrio,
                "medidor": row.medidor,
                "latitud": row.latitud,
                "longitud": row.longitud,
            }
        )

        for bimester, kwh in row.consumos.items():
            lote_counts[bimester.codigo_lote] += 1
            lote_nombres.setdefault(
                bimester.codigo_lote, f"Bimestre {bimester.n} de {bimester.year}"
            )
            consumos.append(
                {
                    "numero_suministro": row.numero_suministro,
                    "codigo_lote": bimester.codigo_lote,
                    "fecha_inicio": bimester.fecha_inicio.isoformat(),
                    "fecha_fin": bimester.fecha_fin.isoformat(),
                    "dias_facturados": bimester.dias_facturados,
                    "kwh": kwh,
                }
            )

    lotes = [
        {"codigo_lote": codigo, "nombre": lote_nombres[codigo], "cantidad_registros": count}
        for codigo, count in sorted(lote_counts.items())
    ]

    return ImportPayloads(
        clientes=list(clientes.values()),
        suministros=suministros,
        lotes=lotes,
        consumos=consumos,
    )
