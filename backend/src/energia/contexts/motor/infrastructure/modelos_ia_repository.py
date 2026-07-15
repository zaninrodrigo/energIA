"""SqlModelosIaRepository: `ModelosIaRepository`'s implementation (Etapa 6, US-011/RF-006,
AI_ENGINE_SPEC.md §9.4) -- `modelos_ia` (DOMAIN_MODEL.md §8.6/§10.5) is the first table `motor`
writes to that ALSO carries a state machine of its own (`Activo`/`Obsoleto`/`Experimental`/
`Retirado`, `ck_modelos_ia_estado`).

**Upsert semantics (idempotent Error-retry reprocess, mirrors `SqlFeatureVectorRepository`'s own
reasoning).** `registrar_fit` upserts on `uq_modelos_ia_nombre_version (nombre, version)`: a
retry of the SAME lote re-derives the SAME `version` (`domain/isolation_forest.py`'s
`construir_version_modelo` is a deterministic function of `codigo_lote` alone), so the retry
reuses the SAME row (refreshing `estado`/`fecha_entrenamiento`) instead of colliding against the
unique constraint or creating a duplicate "version" for what is really the same fit attempt.

**Single-Activo-per-`nombre`, implemented despite no explicit RD-04x invariant.**
DOMAIN_MODEL.md §10.5 ("Versionado del Modelo") lists `Activo`/`Obsoleto`/`Experimental`/
`Retirado` as the entity's states but carries NO "Reglas del Negocio"/"Reglas" subsection at all
(unlike §10.1-§10.3's Feedback/Dataset Etiquetado/Reentrenamiento, which DO enumerate RD-042
through RD-049) -- there is no explicit rule anywhere requiring exactly one `Activo` row per
`nombre`. This implementation adds that invariant anyway, reasoning from the state's OWN semantics
(`"Activo"` reads as "the one currently in effect for this `nombre`", not "one of several
simultaneously in effect") plus RD-048 ("Toda versión debe conservarse") -- so every superseded
fit's row is FLIPPED, never deleted. **`Obsoleto`, not `Retirado`, is the flip target**: `Retirado`
reads as a deliberate, manual decommission (§10.5's `desactivar()` method, an operator action),
while an automatic per-lote refit (DEC-018: "(re)ajuste no supervisado por lote") superseding its
own predecessor is a routine, expected event, not a decommission -- `Obsoleto` ("superseded by a
newer fit, still historically inspectable") is the closer semantic match. `Experimental`/
`Retirado` are never assigned by this implementation at all (no v1 workflow produces either).

**FIX (reviewer finding, CRITICAL, 2026-07-15) -- single-Activo race across concurrent lotes.**
Without further locking, `registrar_fit` had a race: two concurrent transactions processing
DIFFERENT lotes, both fitting the SAME `nombre` (scope), each `INSERT`s its own `Activo` row
(different `version`, so no unique-constraint collision) and then runs `_FLIP_OBSOLETOS_SQL` --
but under `READ COMMITTED`, neither transaction's flip can see the OTHER's not-yet-committed
`INSERT`, so BOTH flips affect zero rows, and BOTH commit, leaving TWO `Activo` rows for the SAME
`nombre` (the single-Activo invariant above, silently broken). The fix takes a **transaction-
scoped advisory lock** keyed on `nombre` (`SELECT pg_advisory_xact_lock(hashtext('modelos_ia:' ||
:nombre))`) at the very start of `registrar_fit`, BEFORE the upsert+flip: this serializes
`registrar_fit` calls that share the SAME `nombre` (the second waits for the first's transaction
to commit or roll back entirely before proceeding), so the flip always sees every prior `Activo`
row for that `nombre` that has already committed. `pg_advisory_xact_lock` releases automatically
at the end of the transaction (commit or rollback) -- no explicit unlock, no `try/finally`. It
only contends between simultaneous fits of the SAME `nombre`: two lotes fitting DIFFERENT scopes
(e.g. `isolation-forest-Residencial` vs. `isolation-forest-Industrial`) hash to different lock
keys and never block each other. `hashtext` is a 32-bit hash -- an astronomically unlikely
collision between two DIFFERENT `nombre` strings would only cost a spurious serialization, never
an incorrect result (both transactions still run the SAME correct upsert+flip logic once
serialized, just against a different `nombre` than intended by the lock key, which is harmless).

**RD-049 gap, not closed here.** RD-049 ("Debe registrarse la configuración utilizada") has no
column to land in -- neither `modelos_ia` nor `reentrenamientos_modelo` carries a hyperparameter
column (AI_ENGINE_SPEC.md §16, gap #3, already registered as v2 debt). This implementation does
NOT introduce one (the design directive: schema is not modified). Etapa 6's hyperparameters are
the FIXED DEC-011/DEC-012/DEC-013 module constants (`domain/isolation_forest.py`) -- identical
for every fit, so "the configuration used" is fully recoverable from the code/docs even though no
per-row column records it; this only becomes a real gap once hyperparameters are ever tuned
per-fit rather than fixed constants (unchanged from the existing §16 assessment).
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Transaction-scoped advisory lock keyed on `nombre` -- module docstring's "FIX" note above.
# `pg_advisory_xact_lock` blocks until acquired and releases automatically at commit/rollback.
_ADVISORY_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtext('modelos_ia:' || :nombre))")

_UPSERT_SQL = text(
    """
    INSERT INTO modelos_ia (nombre, version, algoritmo, estado, fecha_entrenamiento)
    VALUES (:nombre, :version, :algoritmo, 'Activo', now())
    ON CONFLICT (nombre, version) DO UPDATE SET
        estado = 'Activo',
        fecha_entrenamiento = now()
    RETURNING id
    """
)

_FLIP_OBSOLETOS_SQL = text(
    """
    UPDATE modelos_ia
    SET estado = 'Obsoleto'
    WHERE nombre = :nombre
      AND estado = 'Activo'
      AND id <> :nuevo_id
      AND deleted_at IS NULL
    """
)


class SqlModelosIaRepository:
    """`ModelosIaRepository` (domain/ports.py) backed by two statements (upsert + flip), same
    session/transaction as the rest of the request -- see module docstring for the semantics."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def registrar_fit(self, *, nombre: str, version: str, algoritmo: str) -> UUID:
        # Serializes concurrent `registrar_fit` calls for the SAME `nombre` -- module docstring's
        # "FIX" note above -- BEFORE the upsert+flip, so the flip below always sees every prior
        # committed `Activo` row for this `nombre`.
        await self._session.execute(_ADVISORY_LOCK_SQL, {"nombre": nombre})
        result = await self._session.execute(
            _UPSERT_SQL, {"nombre": nombre, "version": version, "algoritmo": algoritmo}
        )
        nuevo_id: UUID = result.scalar_one()
        await self._session.execute(_FLIP_OBSOLETOS_SQL, {"nombre": nombre, "nuevo_id": nuevo_id})
        return nuevo_id
