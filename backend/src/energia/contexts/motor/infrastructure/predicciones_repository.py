"""SqlPrediccionesRepository: `PrediccionesRepository`'s implementation (Etapa 6, US-011/RF-006,
AI_ENGINE_SPEC.md §9) -- `predicciones` (DOMAIN_MODEL.md §8.7).

**Why soft-delete-then-insert, not `ON CONFLICT` (unlike `SqlFeatureVectorRepository`/
`SqlModelosIaRepository`).** `predicciones` (`docker/postgres/init/01_schema.sql`) has no unique
constraint/index beyond its own surrogate `id` -- no `(suministro_id, lote_id)` or
`(suministro_id, lote_id, modelo_ia_id)` uniqueness exists to upsert against. An Error-retry
reprocess of the SAME lote must still be idempotent (mission directive #6): this repository
achieves that by soft-deleting every existing (non-soft-deleted) row for `lote_id` FIRST
(`deleted_at = now()`), then bulk-inserting the fresh set, all in the SAME transaction as the rest
of `ProcesarLote` -- a crash between the two statements rolls back the whole thing (no half-
cleared state persists), the same atomicity guarantee every other write in this bounded context
relies on (AI_ENGINE_SPEC.md §2.5).

**FIX (reviewer finding, CRITICAL, 2026-07-15) -- soft delete, not a physical `DELETE`.** This
repository originally issued `DELETE FROM predicciones WHERE lote_id = :lote_id`, the ONLY
physical `DELETE` anywhere in `src/` -- violating `DATABASE_DESIGN.md` §10 ("ningún `DELETE`
físico") and, worse, reproducing a future crash: once Etapa 7 (not yet implemented) writes
`resultados_ia.prediccion_id` (a `FOREIGN KEY ... REFERENCES predicciones (id)` with the default
`NO ACTION` delete rule, `01_schema.sql`'s `fk_resultados_ia_prediccion`), retrying a lote whose
`predicciones` row is already referenced by a `resultados_ia` row would hit a foreign key
violation on the physical `DELETE` -- permanently stuck, since the retry can never reach its
`INSERT`. `predicciones` already carries the standard audit/soft-delete columns (`created_at`/
`updated_at`/`deleted_at`/`created_by`/`updated_by`, same as every other table in this schema) --
this repository now uses them instead: `reemplazar_lote` `UPDATE`s every existing
(`deleted_at IS NULL`) row for `lote_id` to `deleted_at = now()`, THEN `INSERT`s the fresh set. No
row is ever physically removed, so any `resultados_ia.prediccion_id` referencing an OLD
`predicciones` row keeps pointing at a valid (if soft-deleted) row across a retry -- FK-safe -- and
this now follows the SAME audit convention every other write in this schema already follows. Any
FUTURE read against `predicciones` must filter `WHERE deleted_at IS NULL` (the standard
"active-rows-only" convention this schema uses everywhere else, e.g. `modelos_ia_repository.py`'s
`_FLIP_OBSOLETOS_SQL`) -- none exists in `src/` today: this repository is write-only, and
`ProcesarLote` keeps its own in-memory `PrediccionSuministro` list for the response, never
re-reading the table.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.ports import PrediccionParaGuardar

_SOFT_DELETE_SQL = text(
    "UPDATE predicciones SET deleted_at = now() WHERE lote_id = :lote_id AND deleted_at IS NULL"
)

_INSERT_SQL = text(
    """
    INSERT INTO predicciones (modelo_ia_id, suministro_id, lote_id, score, clasificacion)
    VALUES (:modelo_ia_id, :suministro_id, :lote_id, :score, :clasificacion)
    """
)


class SqlPrediccionesRepository:
    """`PrediccionesRepository` (domain/ports.py) backed by a soft-delete-then-insert pair, same
    session/transaction as the rest of the request -- see module docstring for why."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reemplazar_lote(self, lote_id: UUID, filas: Sequence[PrediccionParaGuardar]) -> None:
        """Soft-delete every existing (non-soft-deleted) `predicciones` row for `lote_id`
        (`deleted_at = now()`, never a physical `DELETE` -- module docstring), then insert `filas`
        (may be empty -- unlike `FeatureVectorRepository.guardar_batch`'s empty-batch no-op, an
        empty `filas` here still must CLEAR any stale rows from a previous attempt, so the
        soft-delete always runs)."""
        await self._session.execute(_SOFT_DELETE_SQL, {"lote_id": lote_id})
        if not filas:
            return
        parametros = [
            {
                "modelo_ia_id": fila.modelo_ia_id,
                "suministro_id": fila.suministro_id,
                "lote_id": fila.lote_id,
                "score": fila.score,
                "clasificacion": fila.clasificacion,
            }
            for fila in filas
        ]
        await self._session.execute(_INSERT_SQL, parametros)
