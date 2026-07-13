"""Unit tests for the ImportConsumos use case (US-004), against fake in-memory ports.

No SQLAlchemy, no database: `ConsumoRepository`, `SuministroDirectory`, `LoteDirectory` and
`LecturaRepository` are all `Protocol`s (domain/ports.py), so the use case is fully testable with
plain fakes -- mirrors `test_import_lecturas.py` exactly, extended with the second (`codigo_lote`)
and third (`fecha_lectura`) resolution steps `ImportConsumos` needs before `Consumo.create()` can
even be attempted.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from energia.contexts.consumos.application.import_consumos import ImportConsumos
from energia.contexts.consumos.domain.consumo import Consumo
from energia.contexts.consumos.domain.lectura import Lectura
from energia.contexts.consumos.domain.ports import ConsumoConflictError, ConsumoSourceRecord

_SUMINISTRO_IDS = {"S1": uuid4(), "S2": uuid4()}
_LOTE_IDS = {"L1": uuid4(), "L2": uuid4()}


@dataclass
class _FakeConsumoRepository:
    """In-memory stand-in for `ConsumoRepository`, keyed by `(suministro_id, fecha_inicio,
    fecha_fin)` -- the composite natural key."""

    _by_key: dict[tuple[UUID, date, date], Consumo] = field(default_factory=dict)
    saved: list[Consumo] = field(default_factory=list)
    poisoned_keys: frozenset[tuple[UUID, date, date]] = frozenset()

    async def get_by_suministro_and_periodo(
        self, suministro_id: UUID, fecha_inicio: date, fecha_fin: date
    ) -> Consumo | None:
        return self._by_key.get((suministro_id, fecha_inicio, fecha_fin))

    async def save(self, consumo: Consumo) -> None:
        key = (consumo.suministro_id, consumo.fecha_inicio, consumo.fecha_fin)
        if key in self.poisoned_keys:
            raise ConsumoConflictError(f"conflicto simulado para {key!r}")
        self._by_key[key] = consumo
        self.saved.append(consumo)

    async def list_active(
        self,
        *,
        limit: int,
        offset: int,
        suministro_id: UUID | None = None,
        lote_id: UUID | None = None,
    ) -> list[Consumo]:
        raise NotImplementedError("not needed by ImportConsumos")

    async def count_active(
        self, *, suministro_id: UUID | None = None, lote_id: UUID | None = None
    ) -> int:
        raise NotImplementedError("not needed by ImportConsumos")


@dataclass
class _FakeSuministroDirectory:
    known: dict[str, UUID] = field(default_factory=lambda: dict(_SUMINISTRO_IDS))

    async def resolve(self, numero_suministro: str) -> UUID | None:
        return self.known.get(numero_suministro)


@dataclass
class _FakeLoteDirectory:
    known: dict[str, UUID] = field(default_factory=lambda: dict(_LOTE_IDS))

    async def resolve(self, codigo_lote: str) -> UUID | None:
        return self.known.get(codigo_lote)


@dataclass
class _FakeLecturaRepository:
    """Only `get_by_suministro_and_fecha` is exercised by `ImportConsumos` -- the rest of
    `LecturaRepository`'s surface is irrelevant here."""

    _by_key: dict[tuple[UUID, date], Lectura] = field(default_factory=dict)

    def seed(self, suministro_id: UUID, fecha_lectura: date) -> Lectura:
        lectura = Lectura.create(
            suministro_id=suministro_id,
            fecha_lectura=fecha_lectura,
            lectura_anterior="0",
            lectura_actual="100",
            dias_facturados=30,
        )
        self._by_key[(suministro_id, fecha_lectura)] = lectura
        return lectura

    async def get_by_suministro_and_fecha(
        self, suministro_id: UUID, fecha_lectura: date
    ) -> Lectura | None:
        return self._by_key.get((suministro_id, fecha_lectura))

    async def save(self, lectura: Lectura) -> None:
        raise NotImplementedError("not needed by ImportConsumos")

    async def list_active(
        self, *, limit: int, offset: int, suministro_id: UUID | None = None
    ) -> list[Lectura]:
        raise NotImplementedError("not needed by ImportConsumos")

    async def count_active(self, *, suministro_id: UUID | None = None) -> int:
        raise NotImplementedError("not needed by ImportConsumos")


class _ListConsumoSource:
    def __init__(self, records: list[ConsumoSourceRecord]) -> None:
        self._records = records

    def fetch(self) -> list[ConsumoSourceRecord]:
        return self._records


def _record(**overrides: object) -> ConsumoSourceRecord:
    kwargs: dict[str, object] = {
        "numero_suministro": "S1",
        "codigo_lote": "L1",
        "fecha_inicio": "2024-01-01",
        "fecha_fin": "2024-01-31",
        "dias_facturados": 31,
        "kwh": "310.000",
    }
    kwargs.update(overrides)
    return ConsumoSourceRecord(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def repository() -> _FakeConsumoRepository:
    return _FakeConsumoRepository()


@pytest.fixture
def suministro_directory() -> _FakeSuministroDirectory:
    return _FakeSuministroDirectory()


@pytest.fixture
def lote_directory() -> _FakeLoteDirectory:
    return _FakeLoteDirectory()


@pytest.fixture
def lectura_repository() -> _FakeLecturaRepository:
    return _FakeLecturaRepository()


def _use_case(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> ImportConsumos:
    return ImportConsumos(
        repository=repository,
        suministro_directory=suministro_directory,
        lote_directory=lote_directory,
        lectura_repository=lectura_repository,
    )


async def test_execute_creates_every_new_valid_record(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource(
        [
            _record(fecha_inicio="2024-01-01", fecha_fin="2024-01-31"),
            _record(fecha_inicio="2024-02-01", fecha_fin="2024-02-29"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert summary.rejected == []
    assert len(repository.saved) == 2


async def test_execute_resolves_suministro_and_lote_ids_onto_the_saved_consumo(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(numero_suministro="S2", codigo_lote="L2")])

    await use_case.execute(source)

    assert repository.saved[0].suministro_id == _SUMINISTRO_IDS["S2"]
    assert repository.saved[0].lote_id == _LOTE_IDS["L2"]


async def test_execute_rejects_a_record_whose_numero_suministro_is_missing(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(numero_suministro=None)])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert any("numero_suministro" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_suministro_does_not_exist(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(numero_suministro="does-not-exist")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert any("suministro inexistente" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_codigo_lote_is_missing(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(codigo_lote=None)])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert any("codigo_lote" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_lote_does_not_exist(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(codigo_lote="does-not-exist")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert any("lote inexistente" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_domain_invalid_record_without_aborting_the_batch(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource(
        [
            _record(fecha_inicio="2024-01-01", fecha_fin="2024-01-31"),
            # fecha_fin < fecha_inicio: domain-invalid.
            _record(fecha_inicio="2024-03-01", fecha_fin="2024-02-01"),
            _record(fecha_inicio="2024-04-01", fecha_fin="2024-04-30"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert len(summary.rejected) == 1
    assert any("fecha_fin" in reason for reason in summary.rejected[0].reasons)


# -- fecha_lectura resolution ---------------------------------------------------------------------


async def test_execute_rejects_a_record_whose_fecha_lectura_is_malformed(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(fecha_lectura="31-01-2024")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert any("fecha_lectura inválida" in reason for reason in summary.rejected[0].reasons)


async def test_execute_resolves_a_given_fecha_lectura_to_its_lectura_id(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    lectura = lectura_repository.seed(_SUMINISTRO_IDS["S1"], date(2024, 1, 31))
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(fecha_lectura="2024-01-31")])

    await use_case.execute(source)

    assert repository.saved[0].lectura_id == lectura.id


async def test_execute_rejects_a_record_whose_fecha_lectura_does_not_resolve(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(fecha_lectura="2024-01-31")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert any("lectura inexistente" in reason for reason in summary.rejected[0].reasons)


async def test_execute_with_fecha_lectura_omitted_leaves_lectura_id_null(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    """Per the schema comment on `consumos.lectura_id`: historical files may not carry lectura
    detail per period -- an omitted `fecha_lectura` must not be treated as a rejection."""
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record()])  # fecha_lectura left at its UNSET default

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert repository.saved[0].lectura_id is None


async def test_execute_with_fecha_lectura_explicitly_null_leaves_lectura_id_null(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(fecha_lectura=None)])

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert repository.saved[0].lectura_id is None


async def test_reimporting_with_fecha_lectura_omitted_preserves_the_existing_lectura_id(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    lectura = lectura_repository.seed(_SUMINISTRO_IDS["S1"], date(2024, 1, 31))
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    await use_case.execute(_ListConsumoSource([_record(fecha_lectura="2024-01-31", kwh="310.000")]))

    summary = await use_case.execute(_ListConsumoSource([_record(kwh="320.000")]))

    assert summary.updated == 1
    final = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert final is not None
    assert final.lectura_id == lectura.id
    assert final.kwh == Decimal("320.000")


async def test_reimporting_with_fecha_lectura_explicitly_null_clears_the_existing_lectura_id(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    lectura_repository.seed(_SUMINISTRO_IDS["S1"], date(2024, 1, 31))
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    await use_case.execute(_ListConsumoSource([_record(fecha_lectura="2024-01-31")]))

    summary = await use_case.execute(_ListConsumoSource([_record(fecha_lectura=None)]))

    assert summary.updated == 1
    final = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert final is not None
    assert final.lectura_id is None


# -- consumo_promedio_diario: UNSET (compute/preserve) vs. null (store/clear) --------------------


async def test_omitted_consumo_promedio_diario_is_computed_on_create(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record(kwh="100", dias_facturados=4)])

    await use_case.execute(source)

    assert repository.saved[0].consumo_promedio_diario == Decimal("25.000")


async def test_reimporting_with_consumo_promedio_diario_omitted_recomputes_it(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    """FIX 1: an omitted `consumo_promedio_diario` on re-import must **recompute** from the
    record's own `kwh`/`dias_facturados`, never preserve whatever average was stored before --
    see `application/import_consumos.py`'s module docstring. The previous (buggy) behavior
    preserved `existing`'s stored average here, exactly like `ImportLotes` correctly does for its
    own opaque fields -- wrong for this field, since it is derived from inputs that are always
    present and already fresh on this very record.
    """
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    await use_case.execute(
        _ListConsumoSource([_record(kwh="310.000", consumo_promedio_diario="9.5")])
    )

    summary = await use_case.execute(_ListConsumoSource([_record(kwh="320.000")]))

    assert summary.updated == 1
    final = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert final is not None
    # 320 / 31 = 10.322580... -> quantized to numeric(12,3).
    assert final.consumo_promedio_diario == Decimal("10.323")
    assert final.kwh == Decimal("320.000")


async def test_reimporting_reproduces_the_reviewer_scenario_kwh_100_to_295_recomputes_promedio(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    """Exact reviewer-reported reproduction of the FIX 1 bug: `kwh` 100 -> 295 (same
    `dias_facturados`, 31), `consumo_promedio_diario` omitted on the re-import. Before the fix
    this returned 3.226 (the first import's stored average, frozen); it must now recompute to
    9.516 from the record's own (fresh) `kwh`/`dias_facturados`."""
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    await use_case.execute(_ListConsumoSource([_record(kwh="100")]))

    summary = await use_case.execute(_ListConsumoSource([_record(kwh="295")]))

    assert summary.updated == 1
    final = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert final is not None
    # 295 / 31 = 9.516129... -> quantized to numeric(12,3).
    assert final.consumo_promedio_diario == Decimal("9.516")
    assert final.kwh == Decimal("295")


async def test_reimporting_with_consumo_promedio_diario_explicitly_null_clears_it(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    await use_case.execute(_ListConsumoSource([_record(consumo_promedio_diario="9.5")]))

    summary = await use_case.execute(_ListConsumoSource([_record(consumo_promedio_diario=None)]))

    assert summary.updated == 1
    final = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert final is not None
    assert final.consumo_promedio_diario is None


# -- idempotency / update / duplicate-within-payload / conflict, mirroring Lectura ---------------


async def test_execute_is_idempotent_on_a_second_identical_run(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource([_record()])

    await use_case.execute(source)
    second_summary = await use_case.execute(source)

    assert second_summary.created == 0
    assert second_summary.updated == 0
    assert second_summary.unchanged == 1


async def test_execute_updates_a_record_whose_data_changed(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    await use_case.execute(_ListConsumoSource([_record(kwh="310.000")]))
    original = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert original is not None

    summary = await use_case.execute(_ListConsumoSource([_record(kwh="400.000")]))

    assert summary.updated == 1
    updated = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert updated is not None
    assert updated.kwh == Decimal("400.000")
    assert updated.id == original.id


async def test_execute_upserts_over_a_duplicate_key_within_the_same_payload(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource(
        [
            _record(kwh="100.000"),
            _record(kwh="200.000"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 1
    final = await repository.get_by_suministro_and_periodo(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1), date(2024, 1, 31)
    )
    assert final is not None
    assert final.kwh == Decimal("200.000")


async def test_execute_rejects_a_record_whose_save_raises_a_conflict_without_aborting_the_batch(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    repository.poisoned_keys = frozenset(
        {(_SUMINISTRO_IDS["S1"], date(2024, 2, 1), date(2024, 2, 29))}
    )
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource(
        [
            _record(fecha_inicio="2024-01-01", fecha_fin="2024-01-31"),
            _record(fecha_inicio="2024-02-01", fecha_fin="2024-02-29"),
            _record(fecha_inicio="2024-03-01", fecha_fin="2024-03-31"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert len(summary.rejected) == 1


async def test_execute_returns_a_zeroed_summary_for_an_empty_source(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)

    summary = await use_case.execute(_ListConsumoSource([]))

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []


async def test_execute_treats_a_different_period_for_the_same_suministro_as_a_different_record(
    repository: _FakeConsumoRepository,
    suministro_directory: _FakeSuministroDirectory,
    lote_directory: _FakeLoteDirectory,
    lectura_repository: _FakeLecturaRepository,
) -> None:
    use_case = _use_case(repository, suministro_directory, lote_directory, lectura_repository)
    source = _ListConsumoSource(
        [
            _record(fecha_inicio="2024-01-01", fecha_fin="2024-01-31"),
            _record(fecha_inicio="2024-02-01", fecha_fin="2024-02-29"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert summary.rejected == []
