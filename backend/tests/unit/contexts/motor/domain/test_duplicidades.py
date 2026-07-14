"""Unit tests for `motor.domain.duplicidades` -- Etapa 2's duplicate/drift detection (US-007,
AI_ENGINE_SPEC.md §5). Each function is pure: hand-built `PeriodoHistorialRow`/
`LecturaHistorialRow`/`LoteConteoRow` in, `PeriodoConflictivo`/`LecturaNearDuplicate`/`DriftLote`
out -- no database involved.

FIX 1 (reviewer finding): `PeriodoHistorialRow`/`LecturaHistorialRow` are now PLAIN rows (no
`LAG`/`prev_*`) -- `detectar_periodos_conflictivos`/`detectar_lecturas_near_duplicate` do a sorted
sweep per suministro instead, enumerating every overlapping/near-duplicate pair, not just adjacent
ones. The reviewer's exact reproductions are covered below (search "reviewer's reproduction").
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

from energia.contexts.motor.domain.duplicidades import (
    VENTANA_DIAS_LECTURA_NEAR_DUPLICATE,
    construir_informe_duplicidades,
    detectar_drift_lotes,
    detectar_lecturas_near_duplicate,
    detectar_periodos_conflictivos,
)
from energia.contexts.motor.domain.ports import (
    LecturaHistorialRow,
    LoteConteoRow,
    PeriodoHistorialRow,
)

_SUMINISTRO = uuid4()


def _periodo(**overrides: object) -> PeriodoHistorialRow:
    defaults: dict[str, object] = {
        "consumo_id": uuid4(),
        "suministro_id": _SUMINISTRO,
        "lote_id": uuid4(),
        "fecha_inicio": date(2024, 2, 1),
        "fecha_fin": date(2024, 2, 29),
    }
    defaults.update(overrides)
    return PeriodoHistorialRow(**defaults)  # type: ignore[arg-type]


def _lectura(**overrides: object) -> LecturaHistorialRow:
    defaults: dict[str, object] = {
        "lectura_id": uuid4(),
        "suministro_id": _SUMINISTRO,
        "fecha_lectura": date(2024, 2, 29),
        "lectura_actual": Decimal("200.000"),
    }
    defaults.update(overrides)
    return LecturaHistorialRow(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------------
# detectar_periodos_conflictivos
# ---------------------------------------------------------------------------------------------


def test_periodos_conflictivos_empty_when_no_previous_period() -> None:
    fila = _periodo()
    assert detectar_periodos_conflictivos([fila]) == ()


def test_periodos_conflictivos_empty_when_periods_do_not_overlap() -> None:
    anterior = _periodo(fecha_inicio=date(2024, 1, 1), fecha_fin=date(2024, 1, 31))
    posterior = _periodo(fecha_inicio=date(2024, 2, 1), fecha_fin=date(2024, 2, 28))
    assert detectar_periodos_conflictivos([anterior, posterior]) == ()


def test_periodos_conflictivos_flags_partial_overlap_in_both_directions() -> None:
    anterior = _periodo(
        consumo_id=uuid4(),
        lote_id=uuid4(),
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
    )
    posterior = _periodo(
        consumo_id=uuid4(),
        lote_id=uuid4(),
        fecha_inicio=date(2024, 1, 15),
        fecha_fin=date(2024, 2, 15),
    )

    resultado = detectar_periodos_conflictivos([anterior, posterior])

    assert len(resultado) == 1
    entrada = resultado[0]
    assert entrada.suministro_id == _SUMINISTRO
    assert len(entrada.periodos) == 2

    por_consumo = {p.consumo_id: p for p in entrada.periodos}
    marca_posterior = por_consumo[posterior.consumo_id]
    assert marca_posterior.fecha_inicio == posterior.fecha_inicio
    assert marca_posterior.fecha_fin == posterior.fecha_fin
    assert marca_posterior.lote_id == posterior.lote_id
    assert marca_posterior.conflicto_con_consumo_id == anterior.consumo_id
    assert marca_posterior.conflicto_con_fecha_inicio == anterior.fecha_inicio
    assert marca_posterior.conflicto_con_fecha_fin == anterior.fecha_fin
    assert marca_posterior.conflicto_con_lote_id == anterior.lote_id

    marca_anterior = por_consumo[anterior.consumo_id]
    assert marca_anterior.fecha_inicio == anterior.fecha_inicio
    assert marca_anterior.fecha_fin == anterior.fecha_fin
    assert marca_anterior.lote_id == anterior.lote_id
    assert marca_anterior.conflicto_con_consumo_id == posterior.consumo_id
    assert marca_anterior.conflicto_con_lote_id == posterior.lote_id


def test_periodos_conflictivos_flags_boundary_same_day_overlap() -> None:
    """Boundary: `fecha_inicio == prev_fecha_fin` is a same-day overlap -- the same boundary rule
    V5 (Etapa 1) uses, generalized here via the domain's sorted sweep (FIX 1)."""
    anterior = _periodo(fecha_inicio=date(2024, 1, 1), fecha_fin=date(2024, 1, 31))
    posterior = _periodo(fecha_inicio=date(2024, 1, 31), fecha_fin=date(2024, 2, 28))
    assert len(detectar_periodos_conflictivos([anterior, posterior])) == 1


def test_periodos_conflictivos_groups_by_suministro() -> None:
    otro_suministro = uuid4()
    fila_a1 = _periodo(
        suministro_id=_SUMINISTRO,
        fecha_inicio=date(2024, 1, 1),
        fecha_fin=date(2024, 1, 31),
    )
    fila_a2 = _periodo(
        suministro_id=_SUMINISTRO,
        fecha_inicio=date(2024, 1, 15),
        fecha_fin=date(2024, 2, 15),
    )
    fila_b = _periodo(
        suministro_id=otro_suministro,
        fecha_inicio=date(2024, 3, 1),
        fecha_fin=date(2024, 3, 31),
    )

    resultado = detectar_periodos_conflictivos([fila_a1, fila_a2, fila_b])

    assert len(resultado) == 1
    assert resultado[0].suministro_id == _SUMINISTRO


def test_periodos_conflictivos_long_period_pairs_with_both_disjoint_shorter_periods() -> None:
    """Reviewer's reproduction (a): a long period (2024-01-01 -> 2024-12-31) overlaps TWO
    disjoint shorter periods elsewhere in the same year. A `LAG`-adjacency comparison against
    only the immediate predecessor would pair the long period with just one of them (whichever
    sorts adjacent to it) and silently miss the other. The domain's sorted sweep (FIX 1) must
    find BOTH overlaps -- (P, P_a) AND (P, P_b) -- but NOT (P_a, P_b), since P_a and P_b do not
    overlap each other."""
    largo = _periodo(fecha_inicio=date(2024, 1, 1), fecha_fin=date(2024, 12, 31))
    p_a = _periodo(fecha_inicio=date(2024, 3, 1), fecha_fin=date(2024, 3, 31))
    p_b = _periodo(fecha_inicio=date(2024, 6, 1), fecha_fin=date(2024, 6, 30))

    resultado = detectar_periodos_conflictivos([largo, p_a, p_b])

    assert len(resultado) == 1
    entrada = resultado[0]
    pares_detectados = {
        frozenset({p.consumo_id, p.conflicto_con_consumo_id}) for p in entrada.periodos
    }
    assert pares_detectados == {
        frozenset({largo.consumo_id, p_a.consumo_id}),
        frozenset({largo.consumo_id, p_b.consumo_id}),
    }
    # The bug this reproduces: (P_a, P_b) must NOT be reported -- they do not overlap.
    assert frozenset({p_a.consumo_id, p_b.consumo_id}) not in pares_detectados
    # Both directions recorded for each of the two genuine pairs -> 4 marks total.
    assert len(entrada.periodos) == 4


def test_periodos_conflictivos_three_mutually_overlapping_periods_yield_all_three_pairs() -> None:
    """Reviewer's reproduction (c): three periods that ALL pairwise overlap must yield all 3
    pairs (P1-P2, P1-P3, P2-P3), 2 marks per pair (both directions) -- 6 marks total."""
    p1 = _periodo(fecha_inicio=date(2024, 1, 1), fecha_fin=date(2024, 2, 28))
    p2 = _periodo(fecha_inicio=date(2024, 1, 15), fecha_fin=date(2024, 3, 15))
    p3 = _periodo(fecha_inicio=date(2024, 2, 1), fecha_fin=date(2024, 3, 31))

    resultado = detectar_periodos_conflictivos([p1, p2, p3])

    assert len(resultado) == 1
    entrada = resultado[0]
    pares_detectados = {
        frozenset({p.consumo_id, p.conflicto_con_consumo_id}) for p in entrada.periodos
    }
    assert pares_detectados == {
        frozenset({p1.consumo_id, p2.consumo_id}),
        frozenset({p1.consumo_id, p3.consumo_id}),
        frozenset({p2.consumo_id, p3.consumo_id}),
    }
    assert len(entrada.periodos) == 6


# ---------------------------------------------------------------------------------------------
# detectar_lecturas_near_duplicate
# ---------------------------------------------------------------------------------------------


def test_lecturas_near_duplicate_empty_when_no_previous_lectura() -> None:
    assert detectar_lecturas_near_duplicate([_lectura()]) == ()


def test_lecturas_near_duplicate_flags_close_dates_with_identical_value() -> None:
    anterior = _lectura(fecha_lectura=date(2024, 2, 1), lectura_actual=Decimal("200.000"))
    posterior = _lectura(fecha_lectura=date(2024, 2, 3), lectura_actual=Decimal("200.000"))

    resultado = detectar_lecturas_near_duplicate([anterior, posterior])

    assert len(resultado) == 1
    assert resultado[0].suministro_id == _SUMINISTRO
    assert resultado[0].lectura_id == posterior.lectura_id
    assert resultado[0].conflicto_con_lectura_id == anterior.lectura_id


def test_lecturas_near_duplicate_flags_at_exact_window_boundary() -> None:
    anterior_fecha = date(2024, 2, 1)
    anterior = _lectura(fecha_lectura=anterior_fecha, lectura_actual=Decimal("200.000"))
    posterior = _lectura(
        fecha_lectura=anterior_fecha + timedelta(days=VENTANA_DIAS_LECTURA_NEAR_DUPLICATE),
        lectura_actual=Decimal("200.000"),
    )
    assert len(detectar_lecturas_near_duplicate([anterior, posterior])) == 1


def test_lecturas_near_duplicate_skips_dates_beyond_window() -> None:
    anterior_fecha = date(2024, 2, 1)
    anterior = _lectura(fecha_lectura=anterior_fecha, lectura_actual=Decimal("200.000"))
    posterior = _lectura(
        fecha_lectura=anterior_fecha + timedelta(days=VENTANA_DIAS_LECTURA_NEAR_DUPLICATE + 1),
        lectura_actual=Decimal("200.000"),
    )
    # One day beyond the window -- must NOT be flagged.
    assert detectar_lecturas_near_duplicate([anterior, posterior]) == ()


def test_lecturas_near_duplicate_skips_different_lectura_actual() -> None:
    anterior = _lectura(fecha_lectura=date(2024, 2, 1), lectura_actual=Decimal("200.000"))
    posterior = _lectura(fecha_lectura=date(2024, 2, 3), lectura_actual=Decimal("250.000"))
    assert detectar_lecturas_near_duplicate([anterior, posterior]) == ()


def test_lecturas_near_duplicate_finds_pair_across_an_interleaved_different_value() -> None:
    """Reviewer's reproduction (b): L1(day0, X), L2(day1, Y), L3(day2, X) -- L2's DIFFERENT
    value would break a `LAG`-adjacency chain between L1 and L3, even though L1 and L3 are a
    genuine near-duplicate pair (same value, 2 days apart, within the 3-day window). The domain's
    sorted sweep (FIX 1) must still find (L1, L3)."""
    dia_0 = date(2024, 2, 1)
    valor_x = Decimal("200.000")
    l1 = _lectura(fecha_lectura=dia_0, lectura_actual=valor_x)
    l2 = _lectura(fecha_lectura=dia_0 + timedelta(days=1), lectura_actual=Decimal("999.000"))
    l3 = _lectura(fecha_lectura=dia_0 + timedelta(days=2), lectura_actual=valor_x)

    resultado = detectar_lecturas_near_duplicate([l1, l2, l3])

    assert len(resultado) == 1
    assert resultado[0].lectura_id == l3.lectura_id
    assert resultado[0].conflicto_con_lectura_id == l1.lectura_id


# ---------------------------------------------------------------------------------------------
# detectar_drift_lotes
# ---------------------------------------------------------------------------------------------


def test_drift_lotes_empty_when_counts_match() -> None:
    fila = LoteConteoRow(
        lote_id=uuid4(), codigo_lote="LOTE-OTRO", cantidad_registros=3, consumos_activos=3
    )
    assert detectar_drift_lotes([fila]) == ()


def test_drift_lotes_flags_mismatch_and_reports_diferencia() -> None:
    fila = LoteConteoRow(
        lote_id=uuid4(), codigo_lote="LOTE-OTRO", cantidad_registros=3, consumos_activos=2
    )

    resultado = detectar_drift_lotes([fila])

    assert len(resultado) == 1
    assert resultado[0].codigo_lote == "LOTE-OTRO"
    assert resultado[0].diferencia == -1


def test_drift_lotes_flags_when_count_grew_too() -> None:
    """A lote can also gain rows (another lote's period migrating INTO it), not just lose them."""
    fila = LoteConteoRow(
        lote_id=uuid4(), codigo_lote="LOTE-OTRO", cantidad_registros=3, consumos_activos=4
    )

    resultado = detectar_drift_lotes([fila])

    assert len(resultado) == 1
    assert resultado[0].diferencia == 1


def test_drift_lotes_skips_lote_with_undeclared_cantidad_registros() -> None:
    """FIX 2 (reviewer finding): `cantidad_registros == 0` is the legitimate "count not declared
    yet" default -- comparing it against a real `consumos_activos` count would flag every lote
    that simply has not declared its count yet, not a genuine drift."""
    fila = LoteConteoRow(
        lote_id=uuid4(), codigo_lote="LOTE-SIN-DECLARAR", cantidad_registros=0, consumos_activos=2
    )
    assert detectar_drift_lotes([fila]) == ()


# ---------------------------------------------------------------------------------------------
# construir_informe_duplicidades
# ---------------------------------------------------------------------------------------------


def test_construir_informe_duplicidades_aggregates_all_three_and_sets_lote_id() -> None:
    lote_id = uuid4()

    informe = construir_informe_duplicidades(lote_id, periodos=[], lecturas=[], conteos_lotes=[])

    assert informe.lote_id == lote_id
    assert informe.periodos_conflictivos == ()
    assert informe.lecturas_near_duplicate == ()
    assert informe.drift_lotes == ()
