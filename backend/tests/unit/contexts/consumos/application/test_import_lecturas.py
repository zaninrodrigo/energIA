"""Unit tests for the ImportLecturas use case, against fake in-memory ports.

No SQLAlchemy, no database: `LecturaRepository` and `SuministroDirectory` are both `Protocol`s
(domain/ports.py), so the use case is fully testable with plain fakes -- mirrors
`contexts/suministros/application/test_import_suministros.py` exactly, keyed by the composite
natural key `(suministro_id, fecha_lectura)` instead of a single natural key.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from energia.contexts.consumos.application.import_lecturas import ImportLecturas
from energia.contexts.consumos.domain.lectura import Lectura
from energia.contexts.consumos.domain.ports import LecturaConflictError, LecturaSourceRecord

_SUMINISTRO_IDS = {"S1": uuid4(), "S2": uuid4()}


@dataclass
class _FakeLecturaRepository:
    """In-memory stand-in for `LecturaRepository`, keyed by `(suministro_id, fecha_lectura)`.

    `_deleted` models soft-deleted rows (DECISION #9, resurrection) -- mirrors
    `contexts.clientes.application.test_import_clientes._FakeClienteRepository` exactly.
    """

    _by_key: dict[tuple[UUID, date], Lectura] = field(default_factory=dict)
    _deleted: list[Lectura] = field(default_factory=list)
    saved: list[Lectura] = field(default_factory=list)
    resurrected: list[Lectura] = field(default_factory=list)
    poisoned_keys: frozenset[tuple[UUID, date]] = frozenset()

    async def get_by_suministro_and_fecha(
        self, suministro_id: UUID, fecha_lectura: date
    ) -> Lectura | None:
        return self._by_key.get((suministro_id, fecha_lectura))

    async def get_most_recently_deleted_by_suministro_and_fecha(
        self, suministro_id: UUID, fecha_lectura: date
    ) -> Lectura | None:
        candidates = [
            lectura
            for lectura in self._deleted
            if (lectura.suministro_id, lectura.fecha_lectura) == (suministro_id, fecha_lectura)
        ]
        return candidates[-1] if candidates else None

    async def save(self, lectura: Lectura) -> None:
        key = (lectura.suministro_id, lectura.fecha_lectura)
        if key in self.poisoned_keys:
            raise LecturaConflictError(f"conflicto simulado para {key!r}")
        self._by_key[key] = lectura
        self.saved.append(lectura)

    async def resurrect(self, lectura: Lectura) -> None:
        key = (lectura.suministro_id, lectura.fecha_lectura)
        if key in self.poisoned_keys:
            raise LecturaConflictError(f"conflicto simulado para {key!r}")
        self._deleted = [item for item in self._deleted if item.id != lectura.id]
        self._by_key[key] = lectura
        self.resurrected.append(lectura)

    async def list_active(
        self, *, limit: int, offset: int, suministro_id: UUID | None = None
    ) -> list[Lectura]:
        raise NotImplementedError("not needed by ImportLecturas")

    async def count_active(self, *, suministro_id: UUID | None = None) -> int:
        raise NotImplementedError("not needed by ImportLecturas")


async def _soft_delete(
    repository: _FakeLecturaRepository, suministro_id: UUID, fecha_lectura: date
) -> Lectura:
    """Test helper: simulate a soft-delete without a real database -- mirrors
    `test_import_clientes._soft_delete` exactly."""
    lectura = repository._by_key.pop((suministro_id, fecha_lectura))
    repository._deleted.append(lectura)
    return lectura


@dataclass
class _FakeSuministroDirectory:
    """In-memory stand-in for `SuministroDirectory`: resolves from a fixed numero_suministro ->
    id map."""

    known: dict[str, UUID] = field(default_factory=lambda: dict(_SUMINISTRO_IDS))

    async def resolve(self, numero_suministro: str) -> UUID | None:
        return self.known.get(numero_suministro)


class _ListLecturaSource:
    """A `LecturaSource` wrapping an in-memory list, standing in for the JSON adapter."""

    def __init__(self, records: list[LecturaSourceRecord]) -> None:
        self._records = records

    def fetch(self) -> list[LecturaSourceRecord]:
        return self._records


def _record(
    numero_suministro: str | None = "S1",
    fecha_lectura: str | None = "2024-01-15",
    lectura_anterior: float | int | str | None = "100.000",
    lectura_actual: float | int | str | None = "150.000",
    dias_facturados: int | str | None = 30,
) -> LecturaSourceRecord:
    return LecturaSourceRecord(
        numero_suministro=numero_suministro,
        fecha_lectura=fecha_lectura,
        lectura_anterior=lectura_anterior,
        lectura_actual=lectura_actual,
        dias_facturados=dias_facturados,
    )


@pytest.fixture
def repository() -> _FakeLecturaRepository:
    return _FakeLecturaRepository()


@pytest.fixture
def suministro_directory() -> _FakeSuministroDirectory:
    return _FakeSuministroDirectory()


def _use_case(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> ImportLecturas:
    return ImportLecturas(repository=repository, suministro_directory=suministro_directory)


async def test_execute_creates_every_new_valid_record(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource(
        [
            _record(fecha_lectura="2024-01-01"),
            _record(fecha_lectura="2024-02-01"),
            _record(fecha_lectura="2024-03-01"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 3
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []
    assert len(repository.saved) == 3


async def test_execute_resolves_suministro_id_onto_the_saved_lectura(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource([_record(numero_suministro="S2")])

    await use_case.execute(source)

    assert repository.saved[0].suministro_id == _SUMINISTRO_IDS["S2"]


async def test_execute_rejects_a_record_whose_numero_suministro_is_missing(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource([_record(numero_suministro=None)])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("numero_suministro" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_suministro_does_not_exist(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource([_record(numero_suministro="does-not-exist")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("suministro inexistente" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_domain_invalid_record_without_aborting_the_batch(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource(
        [
            _record(fecha_lectura="2024-01-01"),
            _record(fecha_lectura="2024-02-01"),
            # 3rd is domain-invalid: lectura_actual < lectura_anterior (RD-013).
            _record(fecha_lectura="2024-03-01", lectura_anterior="200.000", lectura_actual="1.000"),
            _record(fecha_lectura="2024-04-01"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 3
    assert len(summary.rejected) == 1
    assert any("lectura_actual" in reason for reason in summary.rejected[0].reasons)


async def test_execute_is_idempotent_on_a_second_identical_run(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource(
        [_record(fecha_lectura="2024-01-01"), _record(fecha_lectura="2024-02-01")]
    )

    await use_case.execute(source)
    second_summary = await use_case.execute(source)

    assert second_summary.created == 0
    assert second_summary.updated == 0
    assert second_summary.unchanged == 2
    assert len(repository.saved) == 2


async def test_execute_updates_a_record_whose_data_changed(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    await use_case.execute(
        _ListLecturaSource([_record(fecha_lectura="2024-01-01", lectura_actual="150.000")])
    )
    original = await repository.get_by_suministro_and_fecha(_SUMINISTRO_IDS["S1"], date(2024, 1, 1))
    assert original is not None

    summary = await use_case.execute(
        _ListLecturaSource([_record(fecha_lectura="2024-01-01", lectura_actual="175.000")])
    )

    assert summary.created == 0
    assert summary.updated == 1
    assert summary.unchanged == 0
    updated = await repository.get_by_suministro_and_fecha(_SUMINISTRO_IDS["S1"], date(2024, 1, 1))
    assert updated is not None
    assert updated.lectura_actual == Decimal("175.000")
    assert updated.id == original.id


async def test_execute_resurrects_a_soft_deleted_record_preserving_its_identity(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    """DECISION #9: re-importing a soft-deleted `(suministro_id, fecha_lectura)` revives the
    original row (same `id`) instead of creating a brand-new identity."""
    use_case = _use_case(repository, suministro_directory)
    await use_case.execute(
        _ListLecturaSource([_record(fecha_lectura="2024-01-01", lectura_actual="150.000")])
    )
    dead = await _soft_delete(repository, _SUMINISTRO_IDS["S1"], date(2024, 1, 1))

    summary = await use_case.execute(
        _ListLecturaSource([_record(fecha_lectura="2024-01-01", lectura_actual="175.000")])
    )

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.restored == 1
    assert summary.rejected == []
    resurrected = await repository.get_by_suministro_and_fecha(
        _SUMINISTRO_IDS["S1"], date(2024, 1, 1)
    )
    assert resurrected is not None
    assert resurrected.id == dead.id
    assert resurrected.lectura_actual == Decimal("175.000")


async def test_execute_rejects_a_record_whose_resurrect_raises_a_conflict(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    """A `LecturaConflictError` from `repository.resurrect()` (e.g. a race against a concurrent
    insert claiming the same composite natural key) rejects only that record, exactly like
    `save()`."""
    use_case = _use_case(repository, suministro_directory)
    await use_case.execute(_ListLecturaSource([_record(fecha_lectura="2024-05-01")]))
    await _soft_delete(repository, _SUMINISTRO_IDS["S1"], date(2024, 5, 1))
    repository.poisoned_keys = frozenset({(_SUMINISTRO_IDS["S1"], date(2024, 5, 1))})

    summary = await use_case.execute(_ListLecturaSource([_record(fecha_lectura="2024-05-01")]))

    assert summary.restored == 0
    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert (
        await repository.get_by_suministro_and_fecha(_SUMINISTRO_IDS["S1"], date(2024, 5, 1))
        is None
    )


async def test_execute_treats_the_same_fecha_for_a_different_suministro_as_a_different_record(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    """`(suministro_id, fecha_lectura)` is the composite key -- the same date for two different
    suministros must not collide."""
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource(
        [
            _record(numero_suministro="S1", fecha_lectura="2024-01-01"),
            _record(numero_suministro="S2", fecha_lectura="2024-01-01"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert summary.rejected == []


async def test_execute_returns_a_zeroed_summary_for_an_empty_source(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)

    summary = await use_case.execute(_ListLecturaSource([]))

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []


async def test_execute_upserts_over_a_duplicate_key_within_the_same_payload(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource(
        [
            _record(fecha_lectura="2024-01-01", lectura_actual="150.000"),
            _record(fecha_lectura="2024-01-01", lectura_actual="160.000"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 1
    assert summary.rejected == []
    final = await repository.get_by_suministro_and_fecha(_SUMINISTRO_IDS["S1"], date(2024, 1, 1))
    assert final is not None
    assert final.lectura_actual == Decimal("160.000")


async def test_execute_upserting_an_identical_duplicate_within_the_payload_reports_unchanged(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource([_record(), _record()])

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 0
    assert summary.unchanged == 1
    assert summary.rejected == []


async def test_execute_rejects_a_record_whose_save_raises_a_conflict_without_aborting_the_batch(
    repository: _FakeLecturaRepository, suministro_directory: _FakeSuministroDirectory
) -> None:
    repository.poisoned_keys = frozenset({(_SUMINISTRO_IDS["S1"], date(2024, 2, 1))})
    use_case = _use_case(repository, suministro_directory)
    source = _ListLecturaSource(
        [
            _record(fecha_lectura="2024-01-01"),
            _record(fecha_lectura="2024-02-01"),
            _record(fecha_lectura="2024-03-01"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert summary.updated == 0
    assert len(summary.rejected) == 1
    assert await repository.get_by_suministro_and_fecha(_SUMINISTRO_IDS["S1"], date(2024, 1, 1))
    assert not await repository.get_by_suministro_and_fecha(_SUMINISTRO_IDS["S1"], date(2024, 2, 1))
    assert await repository.get_by_suministro_and_fecha(_SUMINISTRO_IDS["S1"], date(2024, 3, 1))
