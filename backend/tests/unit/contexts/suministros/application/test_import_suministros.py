"""Unit tests for the ImportSuministros use case, against fake in-memory ports.

No SQLAlchemy, no database: `SuministroRepository`, `ClienteDirectory` and
`CategoriaTarifariaDirectory` are all `Protocol`s (domain/ports.py), so the use case is fully
testable with plain fakes -- mirrors `contexts/clientes/application/test_import_clientes.py`
exactly, with the extra cross-context/same-context resolution steps `suministros` requires.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4

import pytest

from energia.contexts.suministros.application.import_suministros import ImportSuministros
from energia.contexts.suministros.domain.ports import (
    SuministroConflictError,
    SuministroSourceRecord,
)
from energia.contexts.suministros.domain.suministro import Suministro

_CLIENTE_IDS = {"1": uuid4(), "2": uuid4(), "3": uuid4()}
_CATEGORIA_IDS = {"Residencial": uuid4(), "Comercial": uuid4()}


@dataclass
class _FakeSuministroRepository:
    """In-memory stand-in for `SuministroRepository`, keyed by numero_suministro."""

    _by_numero_suministro: dict[str, Suministro] = field(default_factory=dict)
    saved: list[Suministro] = field(default_factory=list)
    poisoned_numero_suministros: frozenset[str] = frozenset()

    async def get_by_numero_suministro(self, numero_suministro: str) -> Suministro | None:
        return self._by_numero_suministro.get(numero_suministro)

    async def save(self, suministro: Suministro) -> None:
        if suministro.numero_suministro in self.poisoned_numero_suministros:
            raise SuministroConflictError(
                f"conflicto simulado para numero_suministro={suministro.numero_suministro!r}"
            )
        self._by_numero_suministro[suministro.numero_suministro] = suministro
        self.saved.append(suministro)

    async def list_active(
        self, *, limit: int, offset: int, cliente_id: UUID | None = None
    ) -> list[Suministro]:
        raise NotImplementedError("not needed by ImportSuministros")

    async def count_active(self, *, cliente_id: UUID | None = None) -> int:
        raise NotImplementedError("not needed by ImportSuministros")


@dataclass
class _FakeClienteDirectory:
    """In-memory stand-in for `ClienteDirectory`: resolves from a fixed numero_cliente -> id map."""

    known: dict[str, UUID] = field(default_factory=lambda: dict(_CLIENTE_IDS))

    async def resolve(self, numero_cliente: str) -> UUID | None:
        return self.known.get(numero_cliente)


@dataclass
class _FakeCategoriaTarifariaDirectory:
    """In-memory stand-in for `CategoriaTarifariaDirectory`: resolves a fixed nombre -> id map."""

    known: dict[str, UUID] = field(default_factory=lambda: dict(_CATEGORIA_IDS))

    async def resolve(self, nombre: str) -> UUID | None:
        return self.known.get(nombre)


class _ListSuministroSource:
    """A `SuministroSource` wrapping an in-memory list, standing in for the JSON adapter."""

    def __init__(self, records: list[SuministroSourceRecord]) -> None:
        self._records = records

    def fetch(self) -> list[SuministroSourceRecord]:
        return self._records


def _record(
    numero_suministro: str | None,
    numero_cliente: str | None = "1",
    categoria_tarifaria: str | None = "Residencial",
    fecha_alta: str | None = "2024-01-15",
    **kwargs: object,
) -> SuministroSourceRecord:
    return SuministroSourceRecord(  # type: ignore[arg-type]
        numero_suministro=numero_suministro,
        numero_cliente=numero_cliente,
        categoria_tarifaria=categoria_tarifaria,
        fecha_alta=fecha_alta,
        **kwargs,
    )


@pytest.fixture
def repository() -> _FakeSuministroRepository:
    return _FakeSuministroRepository()


@pytest.fixture
def cliente_directory() -> _FakeClienteDirectory:
    return _FakeClienteDirectory()


@pytest.fixture
def categoria_directory() -> _FakeCategoriaTarifariaDirectory:
    return _FakeCategoriaTarifariaDirectory()


def _use_case(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> ImportSuministros:
    return ImportSuministros(
        repository=repository,
        cliente_directory=cliente_directory,
        categoria_directory=categoria_directory,
    )


async def test_execute_creates_every_new_valid_record(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1"), _record("S2"), _record("S3")])

    summary = await use_case.execute(source)

    assert summary.created == 3
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []
    assert len(repository.saved) == 3


async def test_execute_resolves_cliente_id_and_categoria_id_onto_the_saved_suministro(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource(
        [_record("S1", numero_cliente="2", categoria_tarifaria="Comercial")]
    )

    await use_case.execute(source)

    saved = repository.saved[0]
    assert saved.cliente_id == _CLIENTE_IDS["2"]
    assert saved.categoria_tarifaria_id == _CATEGORIA_IDS["Comercial"]


async def test_execute_rejects_a_record_whose_numero_cliente_is_missing(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1", numero_cliente=None)])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("numero_cliente" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_cliente_does_not_exist(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1", numero_cliente="does-not-exist")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("cliente inexistente" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_categoria_tarifaria_is_missing(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1", categoria_tarifaria=None)])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("categoria_tarifaria" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_categoria_tarifaria_does_not_exist(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1", categoria_tarifaria="does-not-exist")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    reasons = summary.rejected[0].reasons
    assert any("categoria tarifaria inexistente" in reason for reason in reasons)


async def test_execute_rejects_a_domain_invalid_record_without_aborting_the_batch(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource(
        [_record("S1"), _record("S2"), _record(None), _record("S3")]  # 3rd is domain-invalid
    )

    summary = await use_case.execute(source)

    assert summary.created == 3
    assert len(summary.rejected) == 1
    assert any("numero_suministro" in reason for reason in summary.rejected[0].reasons)


async def test_execute_is_idempotent_on_a_second_identical_run(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1"), _record("S2"), _record("S3")])

    await use_case.execute(source)
    second_summary = await use_case.execute(source)

    assert second_summary.created == 0
    assert second_summary.updated == 0
    assert second_summary.unchanged == 3
    assert len(repository.saved) == 3


async def test_execute_updates_a_record_whose_data_changed(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    await use_case.execute(_ListSuministroSource([_record("S1", localidad="Old")]))
    original_id = (await repository.get_by_numero_suministro("S1")).id  # type: ignore[union-attr]

    summary = await use_case.execute(_ListSuministroSource([_record("S1", localidad="New")]))

    assert summary.created == 0
    assert summary.updated == 1
    assert summary.unchanged == 0
    updated = await repository.get_by_numero_suministro("S1")
    assert updated is not None
    assert updated.localidad == "New"
    assert updated.id == original_id


async def test_execute_returns_a_zeroed_summary_for_an_empty_source(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)

    summary = await use_case.execute(_ListSuministroSource([]))

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []


async def test_execute_upserts_over_a_duplicate_numero_suministro_within_the_same_payload(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource(
        [_record("S1", localidad="First"), _record("S1", localidad="Second")]
    )

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 1
    assert summary.unchanged == 0
    assert summary.rejected == []
    final = await repository.get_by_numero_suministro("S1")
    assert final is not None
    assert final.localidad == "Second"


async def test_execute_upserting_an_identical_duplicate_within_the_payload_reports_unchanged(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1"), _record("S1")])

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 0
    assert summary.unchanged == 1
    assert summary.rejected == []


async def test_execute_rejects_a_record_whose_save_raises_a_conflict_without_aborting_the_batch(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    repository.poisoned_numero_suministros = frozenset({"S2"})
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1"), _record("S2"), _record("S3")])

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert len(summary.rejected) == 1
    assert summary.rejected[0].record.numero_suministro == "S2"
    assert await repository.get_by_numero_suministro("S1") is not None
    assert await repository.get_by_numero_suministro("S2") is None
    assert await repository.get_by_numero_suministro("S3") is not None


async def test_execute_parses_fecha_alta_onto_the_saved_suministro(
    repository: _FakeSuministroRepository,
    cliente_directory: _FakeClienteDirectory,
    categoria_directory: _FakeCategoriaTarifariaDirectory,
) -> None:
    use_case = _use_case(repository, cliente_directory, categoria_directory)
    source = _ListSuministroSource([_record("S1", fecha_alta="2020-06-30")])

    await use_case.execute(source)

    assert repository.saved[0].fecha_alta == date(2020, 6, 30)
