"""Unit tests for the ImportClientes use case, against a fake in-memory ClienteRepository.

No SQLAlchemy, no database: `ClienteRepository` is a Protocol (domain/ports.py), so the use
case is fully testable with a plain fake — this is exactly the payoff ADR-001 calls out for
Clean Architecture (dominio testeable sin mocks de infraestructura).
"""

from dataclasses import dataclass, field

import pytest

from energia.contexts.clientes.application.import_clientes import ImportClientes
from energia.contexts.clientes.domain.cliente import Cliente
from energia.contexts.clientes.domain.ports import ClienteConflictError, ClienteSourceRecord


@dataclass
class _FakeClienteRepository:
    """In-memory stand-in for `ClienteRepository`, keyed by numero_cliente.

    `poisoned_numero_clientes` lets a test force `save()`/`resurrect()` to raise
    `ClienteConflictError` for a specific natural key, simulating a database-level conflict
    `ImportClientes` must reject without aborting the rest of the batch (see
    `test_execute_rejects_a_record_whose_save_raises_a_conflict_without_aborting_the_batch`).

    `_deleted` models soft-deleted rows (DECISION #9, resurrection): a plain list, not a dict,
    because more than one dead row can share the same `numero_cliente` over time (the partial
    unique index only forbids that among *active* rows) -- `get_most_recently_deleted_by_
    numero_cliente` picks the last one appended, mirroring `ORDER BY deleted_at DESC LIMIT 1`.
    """

    _by_numero_cliente: dict[str, Cliente] = field(default_factory=dict)
    _deleted: list[Cliente] = field(default_factory=list)
    saved: list[Cliente] = field(default_factory=list)
    resurrected: list[Cliente] = field(default_factory=list)
    poisoned_numero_clientes: frozenset[str] = frozenset()

    async def get_by_numero_cliente(self, numero_cliente: str) -> Cliente | None:
        return self._by_numero_cliente.get(numero_cliente)

    async def get_most_recently_deleted_by_numero_cliente(
        self, numero_cliente: str
    ) -> Cliente | None:
        candidates = [c for c in self._deleted if c.numero_cliente == numero_cliente]
        return candidates[-1] if candidates else None

    async def save(self, cliente: Cliente) -> None:
        if cliente.numero_cliente in self.poisoned_numero_clientes:
            raise ClienteConflictError(
                f"conflicto simulado de integridad para numero_cliente={cliente.numero_cliente!r}"
            )
        self._by_numero_cliente[cliente.numero_cliente] = cliente
        self.saved.append(cliente)

    async def resurrect(self, cliente: Cliente) -> None:
        if cliente.numero_cliente in self.poisoned_numero_clientes:
            raise ClienteConflictError(
                f"conflicto simulado de integridad para numero_cliente={cliente.numero_cliente!r}"
            )
        self._deleted = [c for c in self._deleted if c.id != cliente.id]
        self._by_numero_cliente[cliente.numero_cliente] = cliente
        self.resurrected.append(cliente)

    async def list_active(self, *, limit: int, offset: int) -> list[Cliente]:
        raise NotImplementedError("not needed by ImportClientes")

    async def count_active(self) -> int:
        raise NotImplementedError("not needed by ImportClientes")


async def _soft_delete(repository: _FakeClienteRepository, numero_cliente: str) -> Cliente:
    """Test helper: simulate `UPDATE clientes SET deleted_at = now() WHERE numero_cliente = ...`
    without a real database -- moves the active row out of `_by_numero_cliente` and into
    `_deleted`, exactly what a soft-delete does to which bucket a natural-key lookup finds it in.
    """
    cliente = repository._by_numero_cliente.pop(numero_cliente)
    repository._deleted.append(cliente)
    return cliente


class _ListClienteSource:
    """A `ClienteSource` wrapping an in-memory list, standing in for the JSON adapter."""

    def __init__(self, records: list[ClienteSourceRecord]) -> None:
        self._records = records

    def fetch(self) -> list[ClienteSourceRecord]:
        return self._records


def _record(
    numero_cliente: str | None,
    nombre: str | None = "Juana Perez",
    **kwargs: object,
) -> ClienteSourceRecord:
    return ClienteSourceRecord(numero_cliente=numero_cliente, nombre=nombre, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def repository() -> _FakeClienteRepository:
    return _FakeClienteRepository()


async def test_execute_creates_every_new_valid_record(repository: _FakeClienteRepository) -> None:
    use_case = ImportClientes(repository)
    source = _ListClienteSource([_record("1"), _record("2"), _record("3")])

    summary = await use_case.execute(source)

    assert summary.created == 3
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []
    assert len(repository.saved) == 3


async def test_execute_rejects_invalid_records_without_aborting_the_batch(
    repository: _FakeClienteRepository,
) -> None:
    use_case = ImportClientes(repository)
    source = _ListClienteSource(
        [_record("1"), _record("2"), _record(None), _record("3")]  # 3rd record is invalid
    )

    summary = await use_case.execute(source)

    assert summary.created == 3
    assert len(summary.rejected) == 1
    assert any("numero_cliente" in reason for reason in summary.rejected[0].reasons)


async def test_execute_is_idempotent_on_a_second_identical_run(
    repository: _FakeClienteRepository,
) -> None:
    use_case = ImportClientes(repository)
    source = _ListClienteSource([_record("1"), _record("2"), _record("3")])

    await use_case.execute(source)
    second_summary = await use_case.execute(source)  # `source` is a plain list, safe to re-fetch

    assert second_summary.created == 0
    assert second_summary.updated == 0
    assert second_summary.unchanged == 3
    assert len(repository.saved) == 3  # no new saves on the second run


async def test_execute_updates_a_record_whose_data_changed(
    repository: _FakeClienteRepository,
) -> None:
    use_case = ImportClientes(repository)
    await use_case.execute(_ListClienteSource([_record("1", nombre="Old Name")]))
    original_id = (await repository.get_by_numero_cliente("1")).id  # type: ignore[union-attr]

    summary = await use_case.execute(_ListClienteSource([_record("1", nombre="New Name")]))

    assert summary.created == 0
    assert summary.updated == 1
    assert summary.unchanged == 0
    updated = await repository.get_by_numero_cliente("1")
    assert updated is not None
    assert updated.nombre == "New Name"
    assert updated.id == original_id  # identity is preserved across updates


async def test_execute_returns_a_zeroed_summary_for_an_empty_source(
    repository: _FakeClienteRepository,
) -> None:
    use_case = ImportClientes(repository)

    summary = await use_case.execute(_ListClienteSource([]))

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []


async def test_execute_upserts_over_a_duplicate_numero_cliente_within_the_same_payload(
    repository: _FakeClienteRepository,
) -> None:
    """Two records sharing the same `numero_cliente` inside one payload are processed
    sequentially: the second occurrence finds the first (already saved) via
    `get_by_numero_cliente` and upserts over it -- it ends as `updated`, never as an
    unhandled integrity conflict."""
    use_case = ImportClientes(repository)
    source = _ListClienteSource(
        [_record("1", nombre="First Occurrence"), _record("1", nombre="Second Occurrence")]
    )

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 1
    assert summary.unchanged == 0
    assert summary.rejected == []
    final = await repository.get_by_numero_cliente("1")
    assert final is not None
    assert final.nombre == "Second Occurrence"


async def test_execute_upserting_an_identical_duplicate_within_the_payload_reports_unchanged(
    repository: _FakeClienteRepository,
) -> None:
    """A duplicate whose data is identical to the first occurrence counts as `unchanged`, same
    as re-importing identical data across two separate requests."""
    use_case = ImportClientes(repository)
    source = _ListClienteSource([_record("1"), _record("1")])

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 0
    assert summary.unchanged == 1
    assert summary.rejected == []


async def test_execute_resurrects_a_soft_deleted_record_preserving_its_identity(
    repository: _FakeClienteRepository,
) -> None:
    """DECISION #9: re-importing a soft-deleted `numero_cliente` revives the original row (same
    `id`) instead of creating a brand-new identity -- counted as `restored`, not `created`."""
    use_case = ImportClientes(repository)
    await use_case.execute(_ListClienteSource([_record("1", nombre="Original")]))
    dead = await _soft_delete(repository, "1")

    summary = await use_case.execute(_ListClienteSource([_record("1", nombre="Resurrected")]))

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.restored == 1
    assert summary.rejected == []
    resurrected = await repository.get_by_numero_cliente("1")
    assert resurrected is not None
    assert resurrected.id == dead.id
    assert resurrected.nombre == "Resurrected"


async def test_execute_resurrects_only_the_most_recently_deleted_of_several_dead_rows(
    repository: _FakeClienteRepository,
) -> None:
    """Older dead rows sharing the same `numero_cliente` stay dead; only the latest soft-deleted
    one (the last one appended to `_deleted`, standing in for `ORDER BY deleted_at DESC`) is ever
    a resurrection candidate. Two independently dead rows sharing one natural key can arise from,
    e.g., a direct-SQL insert bypassing this use case while the first is already soft-deleted --
    modeled here by seeding `repository._deleted` directly rather than through `execute()` (which,
    once one dead row exists, would resurrect it instead of ever creating a second one)."""
    older_dead = Cliente.create(numero_cliente="1", nombre="Older Dead Row")
    newer_dead = Cliente.create(numero_cliente="1", nombre="Newer Dead Row")
    repository._deleted.extend([older_dead, newer_dead])
    use_case = ImportClientes(repository)

    summary = await use_case.execute(_ListClienteSource([_record("1", nombre="Resurrected")]))

    assert summary.restored == 1
    resurrected = await repository.get_by_numero_cliente("1")
    assert resurrected is not None
    assert resurrected.id == newer_dead.id
    assert resurrected.id != older_dead.id


async def test_execute_resurrecting_with_identical_data_still_reports_restored_not_unchanged(
    repository: _FakeClienteRepository,
) -> None:
    """FIX 3 (pinning the documented guarantee, see `contexts/README.md`): a resurrection always
    writes, even when every field being re-imported already matches the dead row's stored values
    -- `deleted_at` itself is what changes. Re-importing the exact same data onto a soft-deleted
    row must therefore report `restored`, never folded into `unchanged` the way an ordinary
    already-current active row is."""
    use_case = ImportClientes(repository)
    await use_case.execute(_ListClienteSource([_record("1", nombre="Original")]))
    await _soft_delete(repository, "1")

    summary = await use_case.execute(_ListClienteSource([_record("1", nombre="Original")]))

    assert summary.restored == 1
    assert summary.unchanged == 0


async def test_execute_reimporting_an_already_resurrected_record_identically_reports_unchanged(
    repository: _FakeClienteRepository,
) -> None:
    """Once resurrected, the row is active again: a third, identical import finds it through the
    ordinary `get_by_numero_cliente` path, not the dead-row path -- `unchanged`, not `restored`
    again."""
    use_case = ImportClientes(repository)
    await use_case.execute(_ListClienteSource([_record("1", nombre="Original")]))
    await _soft_delete(repository, "1")
    await use_case.execute(_ListClienteSource([_record("1", nombre="Original")]))  # resurrects

    summary = await use_case.execute(_ListClienteSource([_record("1", nombre="Original")]))

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.restored == 0
    assert summary.unchanged == 1


async def test_execute_rejects_a_record_whose_resurrect_raises_a_conflict(
    repository: _FakeClienteRepository,
) -> None:
    """A `ClienteConflictError` from `repository.resurrect()` (e.g. a race against a concurrent
    insert claiming the same natural key) rejects only that record, exactly like `save()`."""
    await ImportClientes(repository).execute(_ListClienteSource([_record("1", nombre="Original")]))
    await _soft_delete(repository, "1")
    repository.poisoned_numero_clientes = frozenset({"1"})

    summary = await ImportClientes(repository).execute(
        _ListClienteSource([_record("1", nombre="Resurrected")])
    )

    assert summary.restored == 0
    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert await repository.get_by_numero_cliente("1") is None


async def test_execute_rejects_a_resurrect_conflict_without_aborting_the_rest_of_the_batch(
    repository: _FakeClienteRepository,
) -> None:
    """FIX 1: a `ClienteConflictError` raised by `repository.resurrect()` (e.g. the
    infrastructure-level lost-update race between two concurrent resurrections of the same dead
    row, guarded by `deleted_at IS NOT NULL` + a `rowcount` check in
    `SqlAlchemyClienteRepository.resurrect()`) rejects only that one record -- the rest of the
    batch, including brand-new records processed alongside it, still lands as `created`."""
    await ImportClientes(repository).execute(_ListClienteSource([_record("1", nombre="Original")]))
    await _soft_delete(repository, "1")
    repository.poisoned_numero_clientes = frozenset({"1"})

    summary = await ImportClientes(repository).execute(
        _ListClienteSource([_record("1", nombre="Resurrected"), _record("2"), _record("3")])
    )

    assert summary.restored == 0
    assert summary.created == 2
    assert len(summary.rejected) == 1
    assert summary.rejected[0].record.numero_cliente == "1"
    assert await repository.get_by_numero_cliente("1") is None
    assert await repository.get_by_numero_cliente("2") is not None
    assert await repository.get_by_numero_cliente("3") is not None


async def test_execute_rejects_a_record_whose_save_raises_a_conflict_without_aborting_the_batch(
    repository: _FakeClienteRepository,
) -> None:
    """`ClienteConflictError` from `repository.save()` (a database-level integrity conflict
    that passed every domain invariant) rejects only that record -- the rest of the batch is
    still created/updated and the summary accounts for everything correctly."""
    repository.poisoned_numero_clientes = frozenset({"2"})
    use_case = ImportClientes(repository)
    source = _ListClienteSource([_record("1"), _record("2"), _record("3")])

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert len(summary.rejected) == 1
    assert summary.rejected[0].record.numero_cliente == "2"
    assert any("2" in reason for reason in summary.rejected[0].reasons)
    assert await repository.get_by_numero_cliente("1") is not None
    assert await repository.get_by_numero_cliente("2") is None
    assert await repository.get_by_numero_cliente("3") is not None
