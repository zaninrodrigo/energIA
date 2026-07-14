"""Motor's own mirror of `consumos`' `Lote` state machine (DOMAIN_MODEL.md §7.4).

`motor` is a bounded context distinct from `consumos` (DOMAIN_MODEL.md §4.4 vs §4.3) even though
both operate on the SAME physical `lotes` table (ADR-006, modular monolith, one database). Per
`contexts/README.md`'s cross-context directory-port pattern, `motor` must not import anything
from `contexts.consumos` (its domain entities, `EstadoLote` included) -- doing so would reach
into another bounded context's own code instead of going through an explicit port. This module
is therefore a deliberate, small duplication of `contexts.consumos.domain.lote.EstadoLote` /
`ALLOWED_TRANSITIONS`, the same way `SqlDirectSuministroDirectory`
(`contexts/consumos/infrastructure/suministro_directory.py`) duplicates knowledge of
`suministros`' shape via raw SQL instead of importing `contexts.suministros`' ORM model. The
single source of truth for the actual set of states and transitions is the database's own
`ck_lotes_estado` CHECK constraint (`docker/postgres/init/01_schema.sql`) plus
`DOMAIN_MODEL.md` §7.4 -- both mirrors must be kept in sync with THAT, not with each other.

STEP 0 correction (AI_ENGINE_SPEC.md §2.2, 2026-07-14): the state machine tracks the ANALYSIS
lifecycle of a lote, not its import lifecycle -- `Pendiente` means "imported, load in progress or
awaiting analysis", `Procesando` means "the motor is currently running its checks",
`Procesado` means "analysis completed successfully" (terminal, RD-010), and `Error` means
"load-or-analysis failure" (retry re-enters at `Procesando`). This is the same four-state,
four-edge map `consumos/domain/lote.py` already declared before the motor existed to actually
drive it -- STEP 0 only corrects AI_ENGINE_SPEC.md's prose description of what the states MEAN,
not the transitions themselves, which were already exactly this.
"""

from enum import StrEnum


class EstadoLote(StrEnum):
    """Mirrors `contexts.consumos.domain.lote.EstadoLote` exactly -- see module docstring."""

    PENDIENTE = "Pendiente"
    PROCESANDO = "Procesando"
    PROCESADO = "Procesado"
    ERROR = "Error"


# Mirrors `contexts.consumos.domain.lote.ALLOWED_TRANSITIONS` exactly -- see module docstring.
# The full map is kept here (not just the two edges `ProcesarLote` actually requests) so this
# mirror stays a complete, honest copy of the state machine, not a partial view that could
# quietly drift out of sync with what it mirrors.
ALLOWED_TRANSITIONS: dict[EstadoLote, frozenset[EstadoLote]] = {
    EstadoLote.PENDIENTE: frozenset({EstadoLote.PROCESANDO}),
    EstadoLote.PROCESANDO: frozenset({EstadoLote.PROCESADO, EstadoLote.ERROR}),
    EstadoLote.PROCESADO: frozenset(),
    EstadoLote.ERROR: frozenset({EstadoLote.PROCESANDO}),
}


class TransicionNoPermitidaError(ValueError):
    """Raised by `validar_transicion` when the requested move is not in `ALLOWED_TRANSITIONS`
    (RD-010). Defense in depth: the actual concurrency guarantee is the optimistic
    `UPDATE ... WHERE estado IN (...)` in `infrastructure/lote_procesamiento.py`; this is a
    pre-flight domain-level check the application layer runs before ever issuing that SQL, so a
    programming error in `ProcesarLote` (requesting an impossible transition) fails fast and
    explicitly instead of silently relying on the SQL `WHERE` clause to no-op.
    """


def validar_transicion(actual: EstadoLote, nuevo: EstadoLote) -> None:
    """Raise `TransicionNoPermitidaError` unless `actual -> nuevo` is in `ALLOWED_TRANSITIONS`."""
    permitido = ALLOWED_TRANSITIONS[actual]
    if nuevo not in permitido:
        raise TransicionNoPermitidaError(
            f"transición de estado no permitida: {actual.value} -> {nuevo.value}"
        )
