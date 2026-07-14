"""Unit tests for energia.tools.synthetic.generator.

No network, no DB: `generate_dataset()` must be pure Python, producing plain JSON-able dict
payloads for the 5 import endpoints plus the ground-truth manifest, importable 100% as-is.
"""

from datetime import date

import pytest

from energia.tools.synthetic.anomalies import AnomalyType
from energia.tools.synthetic.generator import (
    SCALES,
    SyntheticDataset,
    _period_bounds,
    generate_dataset,
    plan_suministros,
)
from energia.tools.synthetic.profiles import Categoria

# -- scale table --------------------------------------------------------------------------------


def test_scale_table_matches_the_documented_counts() -> None:
    assert SCALES["small"].suministros == 100
    assert SCALES["small"].months == 24
    assert SCALES["medium"].suministros == 1000
    assert SCALES["medium"].months == 36
    assert SCALES["large"].suministros == 5000
    assert SCALES["large"].months == 36


# -- calendar / period bounds ---------------------------------------------------------------------


def test_period_bounds_uses_real_calendar_month_lengths() -> None:
    assert _period_bounds(2024, 2) == (date(2024, 2, 1), date(2024, 2, 29), 29)  # leap year
    assert _period_bounds(2023, 2) == (date(2023, 2, 1), date(2023, 2, 28), 28)  # non-leap
    assert _period_bounds(2024, 4) == (date(2024, 4, 1), date(2024, 4, 30), 30)
    assert _period_bounds(2024, 1) == (date(2024, 1, 1), date(2024, 1, 31), 31)


def test_period_bounds_are_contiguous_month_over_month() -> None:
    from datetime import timedelta

    _, fecha_fin_enero, _ = _period_bounds(2024, 1)
    fecha_inicio_febrero, _, _ = _period_bounds(2024, 2)

    assert fecha_inicio_febrero == fecha_fin_enero + timedelta(days=1)


# -- suministro planning ---------------------------------------------------------------------------


def test_plan_suministros_assigns_only_seeded_categoria_names() -> None:
    plans = plan_suministros(seed=1, n_suministros=100, months=24)

    categorias = {plan.categoria for plan in plans}
    assert categorias <= {Categoria.RESIDENCIAL, Categoria.COMERCIAL, Categoria.INDUSTRIAL}


def test_plan_suministros_is_deterministic_given_the_same_seed() -> None:
    first = plan_suministros(seed=42, n_suministros=100, months=24)
    second = plan_suministros(seed=42, n_suministros=100, months=24)

    assert first == second


def test_plan_suministros_produces_at_least_one_anomaly_of_each_type() -> None:
    """Read-first requirement: planted anomalies must include at least one case each of the 4
    injector-backed rules (R1/R2/R3 directly; gradual_decline is the extra non-rule shape) --
    guaranteed by construction, not left to chance, even at the small scale (100 suministros)."""
    plans = plan_suministros(seed=7, n_suministros=100, months=24)

    tipos_presentes = {plan.anomaly.tipo for plan in plans if plan.anomaly is not None}

    assert tipos_presentes == set(AnomalyType)


def test_plan_suministros_anomaly_rate_is_configurable() -> None:
    low_rate = plan_suministros(seed=3, n_suministros=200, months=24, anomaly_rate=0.05)
    high_rate = plan_suministros(seed=3, n_suministros=200, months=24, anomaly_rate=0.20)

    low_count = sum(1 for plan in low_rate if plan.anomaly is not None)
    high_count = sum(1 for plan in high_rate if plan.anomaly is not None)

    assert high_count > low_count


def test_plan_suministros_gives_a_fraction_of_suministros_a_late_fecha_alta() -> None:
    """Cold-start (AI_ENGINE_SPEC.md sec 6.2): some suministros must join partway through the
    dataset window instead of having a full history from month 0."""
    plans = plan_suministros(seed=11, n_suministros=200, months=36)

    late_starters = [plan for plan in plans if plan.entry_month_offset > 0]

    assert len(late_starters) > 0
    assert all(plan.entry_month_offset < 36 for plan in late_starters)


def test_plan_suministros_sub_threshold_rate_is_configurable_and_approximately_respected() -> None:
    """FIX 1(b): of anomalous suministros, ~`sub_threshold_rate` should carry a sub-threshold
    (`_leve`) anomaly instead of a rule-strength one -- checked with generous tolerance since it
    is a random draw, not an exact split."""
    plans = plan_suministros(
        seed=99, n_suministros=2000, months=24, anomaly_rate=0.5, sub_threshold_rate=0.40
    )

    anomalies = [plan.anomaly for plan in plans if plan.anomaly is not None]
    sub_threshold_types = {AnomalyType.SUDDEN_DROP_LEVE, AnomalyType.SPIKE_LEVE}
    sub_threshold_count = sum(1 for anomaly in anomalies if anomaly.tipo in sub_threshold_types)

    ratio = sub_threshold_count / len(anomalies)
    assert 0.30 <= ratio <= 0.50


def test_plan_suministros_identities_carry_the_seed() -> None:
    """FIX 2: seed-encoded natural keys -- see generator.py's docstring, "Seed-encoded natural
    keys"."""
    plans = plan_suministros(seed=42, n_suministros=5, months=24)

    assert all(plan.numero_suministro.startswith("SYN-S42-SUM-") for plan in plans)
    assert all(plan.numero_cliente.startswith("SYN-S42-CLI-") for plan in plans)


def test_plan_suministros_different_seeds_produce_disjoint_suministro_identities() -> None:
    """FIX 2: a different seed must never collide with (and silently upsert over) a previous
    seed's rows through the real import API's natural-key matching."""
    seed_42 = plan_suministros(seed=42, n_suministros=50, months=24)
    seed_43 = plan_suministros(seed=43, n_suministros=50, months=24)

    numeros_42 = {plan.numero_suministro for plan in seed_42}
    numeros_43 = {plan.numero_suministro for plan in seed_43}

    assert numeros_42.isdisjoint(numeros_43)


def test_plan_suministros_skips_planting_on_a_too_short_series() -> None:
    """FIX 3: a 1-month dataset forces every cold-start suministro's own series to length 0
    (`entry_month_offset == months`) -- planning must skip planting there instead of handing
    `_pick_start_index()`/an injector a meaningless (or, pre-fix, crashing) target."""
    plans = plan_suministros(seed=3, n_suministros=100, months=1)

    for plan in plans:
        if plan.entry_month_offset >= 1:  # 0-length series (months - entry_month_offset < 1)
            assert plan.anomaly is None


# -- full dataset generation ------------------------------------------------------------------


@pytest.fixture
def small_dataset() -> SyntheticDataset:
    return generate_dataset(seed=42, scale="small")


def test_generate_dataset_produces_the_documented_counts(small_dataset: SyntheticDataset) -> None:
    assert len(small_dataset.clientes) == 100
    assert len(small_dataset.suministros) == 100
    assert len(small_dataset.lotes) == 24


def test_generate_dataset_kwh_always_equals_the_lectura_delta(
    small_dataset: SyntheticDataset,
) -> None:
    """The single invariant every anomaly (and every non-anomalous month) must respect --
    otherwise Etapa 1's V2 check ("kwh vs delta de lectura") would flag a behavioral anomaly as
    a data-integrity finding, contaminating the ground truth."""
    lecturas_by_key = {
        (lectura["numero_suministro"], lectura["fecha_lectura"]): lectura
        for lectura in small_dataset.lecturas
    }
    for consumo in small_dataset.consumos:
        lectura = lecturas_by_key[(consumo["numero_suministro"], consumo["fecha_lectura"])]
        delta = lectura["lectura_actual"] - lectura["lectura_anterior"]
        assert consumo["kwh"] == pytest.approx(delta, abs=1e-6)


def test_generate_dataset_dias_facturados_matches_lectura_and_real_calendar(
    small_dataset: SyntheticDataset,
) -> None:
    lecturas_by_key = {
        (lectura["numero_suministro"], lectura["fecha_lectura"]): lectura
        for lectura in small_dataset.lecturas
    }
    for consumo in small_dataset.consumos:
        lectura = lecturas_by_key[(consumo["numero_suministro"], consumo["fecha_lectura"])]
        assert consumo["dias_facturados"] == lectura["dias_facturados"]
        assert consumo["dias_facturados"] in {28, 29, 30, 31}


def test_generate_dataset_lote_cantidad_registros_matches_actual_consumo_count(
    small_dataset: SyntheticDataset,
) -> None:
    counts_by_codigo: dict[str, int] = {}
    for consumo in small_dataset.consumos:
        counts_by_codigo[consumo["codigo_lote"]] = (
            counts_by_codigo.get(consumo["codigo_lote"], 0) + 1
        )

    for lote in small_dataset.lotes:
        assert lote["cantidad_registros"] == counts_by_codigo.get(lote["codigo_lote"], 0)


def test_generate_dataset_periods_are_contiguous_per_suministro(
    small_dataset: SyntheticDataset,
) -> None:
    from datetime import timedelta

    periods_by_suministro: dict[str, list[tuple[date, date]]] = {}
    for consumo in small_dataset.consumos:
        periods_by_suministro.setdefault(consumo["numero_suministro"], []).append(
            (date.fromisoformat(consumo["fecha_inicio"]), date.fromisoformat(consumo["fecha_fin"]))
        )

    for periods in periods_by_suministro.values():
        periods.sort()
        for (_, previous_end), (next_start, _) in zip(periods, periods[1:], strict=False):
            assert next_start == previous_end + timedelta(days=1)


def test_generate_dataset_is_deterministic_given_the_same_seed_and_scale() -> None:
    first = generate_dataset(seed=42, scale="small")
    second = generate_dataset(seed=42, scale="small")

    assert first.manifest == second.manifest
    assert first.consumos == second.consumos


# -- FIX 2: seed-encoded identities ------------------------------------------------------------


def test_generate_dataset_identities_carry_the_seed(small_dataset: SyntheticDataset) -> None:
    assert all(c["numero_cliente"].startswith("SYN-S42-CLI-") for c in small_dataset.clientes)
    assert all(s["numero_suministro"].startswith("SYN-S42-SUM-") for s in small_dataset.suministros)
    assert all(lote["codigo_lote"].startswith("LOTE-SYN-S42-") for lote in small_dataset.lotes)


def test_generate_dataset_different_seeds_produce_disjoint_natural_keys() -> None:
    """FIX 2: re-running with a different seed against the same EnergIA instance must never
    upsert over the previous seed's rows -- see generator.py's docstring, "Seed-encoded natural
    keys"."""
    seed_42 = generate_dataset(seed=42, scale="small")
    seed_43 = generate_dataset(seed=43, scale="small")

    suministros_42 = {s["numero_suministro"] for s in seed_42.suministros}
    suministros_43 = {s["numero_suministro"] for s in seed_43.suministros}
    clientes_42 = {c["numero_cliente"] for c in seed_42.clientes}
    clientes_43 = {c["numero_cliente"] for c in seed_43.clientes}

    assert suministros_42.isdisjoint(suministros_43)
    assert clientes_42.isdisjoint(clientes_43)


# -- FIX 3: too-short series never crash the generator -----------------------------------------


def test_generate_dataset_with_a_1_month_cold_start_scenario_does_not_crash() -> None:
    """Reproduces the original IndexError: `months=1` forces every cold-start suministro's own
    series to length 0 (`entry_month_offset == months`); an anomaly could land on one of them,
    handing an injector an empty series (`inject_spike` indexed it directly, pre-fix)."""
    dataset = generate_dataset(seed=3, scale="small", months=1)

    assert len(dataset.suministros) == 100
    suministros_by_numero = {
        s["numero_suministro"]: s
        for s in dataset.manifest["suministros"]  # type: ignore[union-attr]
    }
    for anomalia in dataset.manifest["anomalias"]:  # type: ignore[union-attr]
        suministro = suministros_by_numero[anomalia["numero_suministro"]]  # type: ignore[index]
        assert suministro["entry_month_offset"] < 1  # never planted on a 0-length series


# -- FIX 1(a): realized pct_change is anchored, matches the manifest, and respects thresholds --

_ANCHORED_RULE_TYPES = {"sudden_drop", "spike"}
_ANCHORED_LEVE_TYPES = {"sudden_drop_leve", "spike_leve"}
_RULE_THRESHOLD_RANGES = {
    "sudden_drop": (-0.80, -0.60),
    "spike": (2.0, 4.0),
}
_LEVE_RANGES = {
    "sudden_drop_leve": (-0.50, -0.30),
    "spike_leve": (0.8, 1.6),
}


@pytest.mark.parametrize("seed", range(10))
def test_generate_dataset_realized_pct_change_matches_the_manifest_and_the_threshold(
    seed: int,
) -> None:
    dataset = generate_dataset(seed=seed, scale="small")

    consumos_by_suministro: dict[str, list[dict[str, object]]] = {}
    for consumo in dataset.consumos:
        consumos_by_suministro.setdefault(str(consumo["numero_suministro"]), []).append(consumo)
    for entries in consumos_by_suministro.values():
        entries.sort(key=lambda item: str(item["fecha_inicio"]))

    anomalias = dataset.manifest["anomalias"]
    assert isinstance(anomalias, list)
    anchored_types = _ANCHORED_RULE_TYPES | _ANCHORED_LEVE_TYPES
    anchored_anomalias = [a for a in anomalias if a["tipo"] in anchored_types]
    assert anchored_anomalias  # small scale always plants at least one of each type

    for anomalia in anchored_anomalias:
        tipo = anomalia["tipo"]
        entries = consumos_by_suministro[str(anomalia["numero_suministro"])]
        start_position = next(
            index
            for index, consumo in enumerate(entries)
            if consumo["fecha_inicio"] == anomalia["periodo_inicio"]
        )
        assert start_position >= 1  # anchored types always have a realized previous month

        prev_kwh = entries[start_position - 1]["kwh"]
        first_kwh = entries[start_position]["kwh"]
        realized_pct_change = first_kwh / prev_kwh - 1

        parametros = anomalia["parametros"]
        assert isinstance(parametros, dict)
        assert realized_pct_change == pytest.approx(parametros["pct_change_first_month"])

        if tipo in _RULE_THRESHOLD_RANGES:
            low, high = _RULE_THRESHOLD_RANGES[tipo]
            assert low - 1e-6 <= realized_pct_change <= high + 1e-6
        else:
            low, high = _LEVE_RANGES[tipo]
            assert low - 1e-6 <= realized_pct_change <= high + 1e-6
            if tipo == "sudden_drop_leve":
                assert realized_pct_change > -0.60  # below R2's own trigger
            else:
                assert realized_pct_change < 2.0  # below R3's own trigger
