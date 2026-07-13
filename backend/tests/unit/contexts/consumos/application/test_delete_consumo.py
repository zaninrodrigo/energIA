"""Unit tests for the DeleteConsumo use case (DECISION #13), against a fake in-memory port.

No SQLAlchemy, no database: `ConsumoRepository` is a `Protocol` (domain/ports.py), so the use
case is fully testable with a plain fake -- mirrors every other application-layer test in this
codebase (ADR-001's payoff).
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from energia.contexts.consumos.application.delete_consumo import DeleteConsumo


@dataclass
class _FakeConsumoRepository:
    """Only `soft_delete` is exercised by `DeleteConsumo` -- the rest of `ConsumoRepository`'s
    surface is irrelevant here."""

    active_ids: set[UUID] = field(default_factory=set)
    soft_deleted_ids: list[UUID] = field(default_factory=list)

    async def soft_delete(self, consumo_id: UUID) -> bool:
        if consumo_id not in self.active_ids:
            return False
        self.active_ids.discard(consumo_id)
        self.soft_deleted_ids.append(consumo_id)
        return True


@pytest.fixture
def repository() -> _FakeConsumoRepository:
    return _FakeConsumoRepository()


async def test_execute_soft_deletes_an_active_consumo_and_returns_true(
    repository: _FakeConsumoRepository,
) -> None:
    consumo_id = uuid4()
    repository.active_ids.add(consumo_id)
    use_case = DeleteConsumo(repository)

    deleted = await use_case.execute(consumo_id)

    assert deleted is True
    assert consumo_id in repository.soft_deleted_ids
    assert consumo_id not in repository.active_ids


async def test_execute_returns_false_for_an_unknown_id(
    repository: _FakeConsumoRepository,
) -> None:
    use_case = DeleteConsumo(repository)

    deleted = await use_case.execute(uuid4())

    assert deleted is False


async def test_execute_returns_false_for_an_already_deleted_consumo(
    repository: _FakeConsumoRepository,
) -> None:
    consumo_id = uuid4()
    repository.active_ids.add(consumo_id)
    use_case = DeleteConsumo(repository)
    assert await use_case.execute(consumo_id) is True

    deleted_again = await use_case.execute(consumo_id)

    assert deleted_again is False
