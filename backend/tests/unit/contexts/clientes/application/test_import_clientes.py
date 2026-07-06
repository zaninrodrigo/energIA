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

    `poisoned_numero_clientes` lets a test force `save()` to raise `ClienteConflictError` for a
    specific natural key, simulating a database-level conflict `ImportClientes` must reject
    without aborting the rest of the batch (see `test_execute_rejects_a_record_whose_save_
    raises_a_conflict_without_aborting_the_batch`).
    """

    _by_numero_cliente: dict[str, Cliente] = field(default_factory=dict)
    saved: list[Cliente] = field(default_factory=list)
    poisoned_numero_clientes: frozenset[str] = frozenset()

    async def get_by_numero_cliente(self, numero_cliente: str) -> Cliente | None:
        return self._by_numero_cliente.get(numero_cliente)

    async def save(self, cliente: Cliente) -> None:
        if cliente.numero_cliente in self.poisoned_numero_clientes:
            raise ClienteConflictError(
                f"conflicto simulado de integridad para numero_cliente={cliente.numero_cliente!r}"
            )
        self._by_numero_cliente[cliente.numero_cliente] = cliente
        self.saved.append(cliente)

    async def list_active(self, *, limit: int, offset: int) -> list[Cliente]:
        raise NotImplementedError("not needed by ImportClientes")

    async def count_active(self) -> int:
        raise NotImplementedError("not needed by ImportClientes")


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
