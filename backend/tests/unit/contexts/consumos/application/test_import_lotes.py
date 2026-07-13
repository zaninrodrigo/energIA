"""Unit tests for the ImportLotes use case, against a fake in-memory port.

No SQLAlchemy, no database: `LoteRepository` is a `Protocol` (domain/ports.py), so the use case
is fully testable with a plain fake -- mirrors `contexts/consumos/application/
test_import_lecturas.py`, minus any directory-port fake (`Lote` has no foreign key to resolve).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from energia.contexts.consumos.application.import_lotes import ImportLotes
from energia.contexts.consumos.domain.lote import EstadoLote, Lote
from energia.contexts.consumos.domain.ports import UNSET, LoteConflictError, LoteSourceRecord


@dataclass
class _FakeLoteRepository:
    """In-memory stand-in for `LoteRepository`, keyed by `codigo_lote`."""

    _by_key: dict[str, Lote] = field(default_factory=dict)
    saved: list[Lote] = field(default_factory=list)
    poisoned_keys: frozenset[str] = frozenset()

    async def get_by_codigo_lote(self, codigo_lote: str) -> Lote | None:
        return self._by_key.get(codigo_lote)

    async def save(self, lote: Lote) -> None:
        if lote.codigo_lote in self.poisoned_keys:
            raise LoteConflictError(f"conflicto simulado para {lote.codigo_lote!r}")
        self._by_key[lote.codigo_lote] = lote
        self.saved.append(lote)

    async def list_active(
        self, *, limit: int, offset: int, estado: EstadoLote | None = None
    ) -> list[Lote]:
        raise NotImplementedError("not needed by ImportLotes")

    async def count_active(self, *, estado: EstadoLote | None = None) -> int:
        raise NotImplementedError("not needed by ImportLotes")


class _ListLoteSource:
    """A `LoteSource` wrapping an in-memory list, standing in for the JSON adapter."""

    def __init__(self, records: list[LoteSourceRecord]) -> None:
        self._records = records

    def fetch(self) -> list[LoteSourceRecord]:
        return self._records


def _record(
    codigo_lote: str | None = "LOTE-2024-01",
    nombre: str | None = "Enero 2024",
    cantidad_registros: int | str | None = 100,
) -> LoteSourceRecord:
    return LoteSourceRecord(
        codigo_lote=codigo_lote, nombre=nombre, cantidad_registros=cantidad_registros
    )


@pytest.fixture
def repository() -> _FakeLoteRepository:
    return _FakeLoteRepository()


def _use_case(repository: _FakeLoteRepository) -> ImportLotes:
    return ImportLotes(repository=repository)


async def test_execute_creates_every_new_valid_record(repository: _FakeLoteRepository) -> None:
    use_case = _use_case(repository)
    source = _ListLoteSource(
        [
            _record(codigo_lote="LOTE-01"),
            _record(codigo_lote="LOTE-02"),
            _record(codigo_lote="LOTE-03"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 3
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []
    assert len(repository.saved) == 3


async def test_execute_created_records_are_born_pendiente(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)

    await use_case.execute(_ListLoteSource([_record()]))

    assert repository.saved[0].estado is EstadoLote.PENDIENTE


async def test_execute_rejects_a_record_whose_codigo_lote_is_missing(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    source = _ListLoteSource([_record(codigo_lote=None)])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("codigo_lote" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_whose_codigo_lote_is_blank(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    source = _ListLoteSource([_record(codigo_lote="   ")])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("codigo_lote" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_record_with_a_negative_cantidad_registros(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    source = _ListLoteSource([_record(cantidad_registros=-5)])

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("cantidad_registros" in reason for reason in summary.rejected[0].reasons)


async def test_execute_rejects_a_domain_invalid_record_without_aborting_the_batch(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    source = _ListLoteSource(
        [
            _record(codigo_lote="LOTE-01"),
            _record(codigo_lote=None),  # invalid: missing codigo_lote
            _record(codigo_lote="LOTE-02"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert len(summary.rejected) == 1


async def test_execute_is_idempotent_on_a_second_identical_run(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    source = _ListLoteSource([_record(codigo_lote="LOTE-01"), _record(codigo_lote="LOTE-02")])

    await use_case.execute(source)
    second_summary = await use_case.execute(source)

    assert second_summary.created == 0
    assert second_summary.updated == 0
    assert second_summary.unchanged == 2
    assert len(repository.saved) == 2


async def test_execute_updates_a_record_whose_nombre_changed(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    await use_case.execute(_ListLoteSource([_record(nombre="Original")]))
    original = await repository.get_by_codigo_lote("LOTE-2024-01")
    assert original is not None

    summary = await use_case.execute(_ListLoteSource([_record(nombre="Actualizado")]))

    assert summary.created == 0
    assert summary.updated == 1
    assert summary.unchanged == 0
    updated = await repository.get_by_codigo_lote("LOTE-2024-01")
    assert updated is not None
    assert updated.nombre == "Actualizado"
    assert updated.id == original.id


async def test_execute_updates_a_record_whose_cantidad_registros_changed(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    await use_case.execute(_ListLoteSource([_record(cantidad_registros=100)]))

    summary = await use_case.execute(_ListLoteSource([_record(cantidad_registros=200)]))

    assert summary.updated == 1
    updated = await repository.get_by_codigo_lote("LOTE-2024-01")
    assert updated is not None
    assert updated.cantidad_registros == 200


async def test_execute_updating_an_existing_lote_never_resets_its_estado(
    repository: _FakeLoteRepository,
) -> None:
    """The core estado-preservation invariant (RD-010's intent): an import re-run must never
    reset a `Procesado` (or any non-`Pendiente`) lote back to `Pendiente`, even though a freshly
    `Lote.create()`d candidate is always `Pendiente`."""
    use_case = _use_case(repository)
    await use_case.execute(_ListLoteSource([_record(cantidad_registros=100)]))
    original = await repository.get_by_codigo_lote("LOTE-2024-01")
    assert original is not None
    procesado = original.transition_to(EstadoLote.PROCESANDO).transition_to(EstadoLote.PROCESADO)
    await repository.save(procesado)

    summary = await use_case.execute(_ListLoteSource([_record(cantidad_registros=200)]))

    assert summary.updated == 1
    updated = await repository.get_by_codigo_lote("LOTE-2024-01")
    assert updated is not None
    assert updated.estado is EstadoLote.PROCESADO
    assert updated.cantidad_registros == 200


async def test_execute_updating_an_existing_lote_preserves_its_original_fecha_importacion(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    original_fecha = datetime(2024, 1, 1, tzinfo=UTC)
    await repository.save(
        Lote.create(
            codigo_lote="LOTE-2024-01",
            cantidad_registros=1,
            fecha_importacion=original_fecha,
        )
    )

    summary = await use_case.execute(_ListLoteSource([_record(cantidad_registros=2)]))

    assert summary.updated == 1
    updated = await repository.get_by_codigo_lote("LOTE-2024-01")
    assert updated is not None
    assert updated.fecha_importacion == original_fecha
    assert datetime.now(UTC) - original_fecha > timedelta(seconds=0)


async def test_execute_returns_a_zeroed_summary_for_an_empty_source(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)

    summary = await use_case.execute(_ListLoteSource([]))

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 0
    assert summary.rejected == []


async def test_execute_upserts_over_a_duplicate_key_within_the_same_payload(
    repository: _FakeLoteRepository,
) -> None:
    use_case = _use_case(repository)
    source = _ListLoteSource(
        [
            _record(codigo_lote="LOTE-2024-01", cantidad_registros=100),
            _record(codigo_lote="LOTE-2024-01", cantidad_registros=150),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 1
    assert summary.updated == 1
    assert summary.rejected == []
    final = await repository.get_by_codigo_lote("LOTE-2024-01")
    assert final is not None
    assert final.cantidad_registros == 150


async def test_execute_rejects_a_record_whose_save_raises_a_conflict_without_aborting_the_batch(
    repository: _FakeLoteRepository,
) -> None:
    repository.poisoned_keys = frozenset({"LOTE-02"})
    use_case = _use_case(repository)
    source = _ListLoteSource(
        [
            _record(codigo_lote="LOTE-01"),
            _record(codigo_lote="LOTE-02"),
            _record(codigo_lote="LOTE-03"),
        ]
    )

    summary = await use_case.execute(source)

    assert summary.created == 2
    assert len(summary.rejected) == 1
    assert await repository.get_by_codigo_lote("LOTE-01") is not None
    assert await repository.get_by_codigo_lote("LOTE-02") is None
    assert await repository.get_by_codigo_lote("LOTE-03") is not None


# -- FIX 1: omitted (UNSET) vs. explicitly-null fields on re-import -------------------------


async def test_execute_creates_a_record_with_omitted_fields_using_documented_defaults(
    repository: _FakeLoteRepository,
) -> None:
    """`nombre`/`cantidad_registros` genuinely `UNSET` (not sent at all) on a brand-new
    `codigo_lote` get the documented create-time defaults (`nombre=None`,
    `cantidad_registros=0`) -- same as `Lote.create()`'s own defaults, one layer up."""
    use_case = _use_case(repository)
    source = _ListLoteSource([LoteSourceRecord(codigo_lote="LOTE-OMIT-01")])

    summary = await use_case.execute(source)

    assert summary.created == 1
    saved = await repository.get_by_codigo_lote("LOTE-OMIT-01")
    assert saved is not None
    assert saved.nombre is None
    assert saved.cantidad_registros == 0


async def test_execute_reimporting_with_nombre_omitted_preserves_the_existing_value(
    repository: _FakeLoteRepository,
) -> None:
    """The core FIX 1 regression: re-importing an existing `codigo_lote` with `nombre` merely
    *absent* from the payload (`UNSET`, not explicit `null`) must not wipe it -- only
    `cantidad_registros` is being updated here."""
    use_case = _use_case(repository)
    await use_case.execute(
        _ListLoteSource(
            [LoteSourceRecord(codigo_lote="LOTE-OMIT-02", nombre="Original", cantidad_registros=1)]
        )
    )

    summary = await use_case.execute(
        _ListLoteSource([LoteSourceRecord(codigo_lote="LOTE-OMIT-02", cantidad_registros=2)])
    )

    assert summary.updated == 1
    updated = await repository.get_by_codigo_lote("LOTE-OMIT-02")
    assert updated is not None
    assert updated.nombre == "Original"
    assert updated.cantidad_registros == 2


async def test_execute_reimporting_with_nombre_explicitly_null_wipes_it(
    repository: _FakeLoteRepository,
) -> None:
    """The other half of FIX 1's contract: an *explicit* `null` for `nombre` (as opposed to
    `UNSET`) does overwrite the stored value -- the caller genuinely asked for it to be cleared."""
    use_case = _use_case(repository)
    await use_case.execute(
        _ListLoteSource(
            [LoteSourceRecord(codigo_lote="LOTE-OMIT-03", nombre="Original", cantidad_registros=1)]
        )
    )

    summary = await use_case.execute(
        _ListLoteSource(
            [LoteSourceRecord(codigo_lote="LOTE-OMIT-03", nombre=None, cantidad_registros=1)]
        )
    )

    assert summary.updated == 1
    updated = await repository.get_by_codigo_lote("LOTE-OMIT-03")
    assert updated is not None
    assert updated.nombre is None


async def test_execute_reimporting_with_cantidad_registros_omitted_preserves_the_existing_value(
    repository: _FakeLoteRepository,
) -> None:
    """Same regression as `nombre`, for `cantidad_registros`: omitted (`UNSET`) on re-import must
    not reset it to the domain default (`0`)."""
    use_case = _use_case(repository)
    await use_case.execute(
        _ListLoteSource(
            [LoteSourceRecord(codigo_lote="LOTE-OMIT-04", nombre="Original", cantidad_registros=50)]
        )
    )

    summary = await use_case.execute(
        _ListLoteSource([LoteSourceRecord(codigo_lote="LOTE-OMIT-04", nombre="Actualizado")])
    )

    assert summary.updated == 1
    updated = await repository.get_by_codigo_lote("LOTE-OMIT-04")
    assert updated is not None
    assert updated.cantidad_registros == 50
    assert updated.nombre == "Actualizado"


async def test_execute_rejects_an_explicit_null_cantidad_registros_on_create(
    repository: _FakeLoteRepository,
) -> None:
    """`cantidad_registros` is a `NOT NULL` column with its own `DEFAULT 0` -- an explicit `null`
    is not a value it can ever hold. Unlike an omitted field (which legitimately means "apply the
    default"), an explicit `null` is rejected outright, even on a brand-new `codigo_lote`."""
    use_case = _use_case(repository)
    source = _ListLoteSource(
        [LoteSourceRecord(codigo_lote="LOTE-OMIT-05", nombre="X", cantidad_registros=None)]
    )

    summary = await use_case.execute(source)

    assert summary.created == 0
    assert len(summary.rejected) == 1
    assert any("cantidad_registros" in reason for reason in summary.rejected[0].reasons)
    assert await repository.get_by_codigo_lote("LOTE-OMIT-05") is None


async def test_execute_rejects_an_explicit_null_cantidad_registros_on_update(
    repository: _FakeLoteRepository,
) -> None:
    """Same rejection, but against an existing row: the existing `cantidad_registros` must stay
    untouched, since the whole record is rejected instead of merged."""
    use_case = _use_case(repository)
    await use_case.execute(
        _ListLoteSource(
            [LoteSourceRecord(codigo_lote="LOTE-OMIT-06", nombre="Original", cantidad_registros=7)]
        )
    )

    summary = await use_case.execute(
        _ListLoteSource(
            [
                LoteSourceRecord(
                    codigo_lote="LOTE-OMIT-06", nombre="Original", cantidad_registros=None
                )
            ]
        )
    )

    assert summary.updated == 0
    assert summary.unchanged == 0
    assert len(summary.rejected) == 1
    assert any("cantidad_registros" in reason for reason in summary.rejected[0].reasons)
    unchanged_row = await repository.get_by_codigo_lote("LOTE-OMIT-06")
    assert unchanged_row is not None
    assert unchanged_row.cantidad_registros == 7


async def test_execute_reimporting_with_every_field_omitted_reports_unchanged(
    repository: _FakeLoteRepository,
) -> None:
    """Counting must still be correct under a partial payload: if every provided field is
    `UNSET` (so nothing to merge in changes), re-running the import reports `unchanged`, not
    `updated` -- this is the same idempotence guarantee `test_execute_is_idempotent_on_a_second_
    identical_run` already covers for a fully-specified payload, now proven for a payload that
    omits everything but the natural key."""
    use_case = _use_case(repository)
    await use_case.execute(
        _ListLoteSource(
            [LoteSourceRecord(codigo_lote="LOTE-OMIT-07", nombre="Original", cantidad_registros=9)]
        )
    )

    summary = await use_case.execute(
        _ListLoteSource([LoteSourceRecord(codigo_lote="LOTE-OMIT-07")])
    )

    assert summary.created == 0
    assert summary.updated == 0
    assert summary.unchanged == 1
    unchanged_row = await repository.get_by_codigo_lote("LOTE-OMIT-07")
    assert unchanged_row is not None
    assert unchanged_row.nombre == "Original"
    assert unchanged_row.cantidad_registros == 9


async def test_execute_omitted_fields_default_to_unset_via_the_record_helper() -> None:
    """Sanity check on this test module's own `_record()` helper: every other test in this file
    supplies `nombre`/`cantidad_registros` explicitly, so this confirms `LoteSourceRecord`'s own
    default (used by tests that construct it directly, without `_record()`) is `UNSET`, not
    `None` -- the dataclass-level half of FIX 1."""
    record = LoteSourceRecord(codigo_lote="LOTE-OMIT-08")

    assert record.nombre is UNSET
    assert record.cantidad_registros is UNSET
