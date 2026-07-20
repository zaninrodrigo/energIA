"""SqlResultadosIaRepository: `ResultadosIaRepository`'s implementation (Etapas 7-8, AI_ENGINE_
SPEC.md §10/§11/§14) -- THE convergence write: `resultados_ia`, `feature_vectors.resultado_ia_id`
(backfill), `anomalias`, `ire`, `impacto_economico`, all for one lote's analyzed suministros, in
ONE call, same session/transaction as the rest of `ProcesarLote`.

**Write order (§14's own table, "Orden de escritura")**: `resultados_ia` (upsert, RETURNING id
per row) -> backfill `feature_vectors.resultado_ia_id` -> `anomalias` (soft-delete-then-insert) ->
`ire` (upsert) -> `impacto_economico` (soft-delete-then-conditional-insert). This repository issues
exactly that sequence, within the SAME transaction its caller already holds -- no commit here.

**Per-table write strategy, and why each one differs:**

- **`resultados_ia`**: upsert on `uq_resultados_ia_suministro_lote (suministro_id, lote_id)`
  (RD-023: "no puede existir más de un ResultadoIA por suministro y lote") -- a retry of the SAME
  lote reuses the SAME row (`id` untouched by the `DO UPDATE`, only its other columns refresh),
  which is exactly what keeps `anomalias`/`ire`/`impacto_economico`'s own FKs stable across a
  retry. Issued ONE ROW AT A TIME (`RETURNING id`), not a single multi-row statement: a raw
  `text()` multi-row `INSERT ... VALUES (...), (...) ... RETURNING id` executed as a driver-level
  executemany does not reliably correlate returned rows back to their own input row by position
  across drivers/PgBouncer modes -- the SAME reasoning `infrastructure/modelos_ia_repository.py`'s
  `registrar_fit` already applies (one `execute` + `scalar_one()` per row, there over a handful of
  scope groups, here over the lote's whole analyzed population). Documented, honest v1 performance
  trade-off: acceptable for this project's synthetic-scale validation runs (hundreds of
  suministros), a real bottleneck candidate once RNF-007 (500.000 suministros/lote) needs
  validating against real load (§12's own "a validar con pruebas de carga" caveat already flags
  Etapa 7-8's persistence step as an estimate, not a measured budget).
- **`feature_vectors.resultado_ia_id`** (backfill): a plain batched `UPDATE` (executemany, no
  `RETURNING` needed) keyed on `(suministro_id, lote_id, version)` -- `feature_vectors` was
  written by Etapa 3-4 with this column `NULL` (§14: "feature_vectors (con resultado_ia_id
  nulo)"); this is the ONE place it gets filled in, using the `resultados_ia.id` this method's own
  upsert loop just resolved (new OR pre-existing, either way correct).
- **`anomalias`**: soft-delete-then-insert, mirroring `SqlPrediccionesRepository`'s own documented
  reasoning (`infrastructure/predicciones_repository.py`) -- `anomalias` carries no natural unique
  key to upsert against (a suministro can have zero to several hits, and their SET can shrink or
  grow across a retry), so a retry must first soft-delete (`deleted_at = now()`, never a physical
  `DELETE` -- DATABASE_DESIGN.md §10) every ACTIVE `anomalias` row belonging to THIS LOTE's
  `resultados_ia` rows, then insert the fresh set. Scoped via a `resultado_ia_id IN (SELECT id
  FROM resultados_ia WHERE lote_id = :lote_id)` subquery -- ONE statement for the whole lote,
  mirroring `predicciones`' own per-lote (not per-suministro) soft-delete scope.
- **`ire`**: a straight upsert on `uq_ire_resultado_ia (resultado_ia_id)` -- unlike `anomalias`/
  `impacto_economico`, EVERY analyzed suministro always gets exactly one `ire` row (`domain/
  ire.py`'s `componer_ire` never skips a suministro: even an all-null feature vector still
  composes SOME IRE, possibly a low one), so there is no "sometimes zero rows" case to handle via
  soft-delete -- a plain `ON CONFLICT (resultado_ia_id) DO UPDATE` is sufficient and simpler.
- **`impacto_economico`**: soft-delete-then-CONDITIONAL-insert (the one hybrid of the two
  strategies above) -- unlike `ire`, a cold-start suministro (`domain/ire.py`'s `calcular_iee_kwh`
  returning `None`) gets NO `impacto_economico` row at all. The soft-delete-first step (scoped to
  the lote, same subquery pattern as `anomalias`) means a suministro that HAD an IEE on a previous
  attempt but does not this time (a data-shape change across a retry, or Etapa 3-4's history
  changing) correctly ends up with NO active `impacto_economico` row after this call, something a
  plain `ON CONFLICT` upsert cannot express (it can only ever ADD/UPDATE a row, never remove one
  that should no longer exist).

**No commit here** -- same session/transaction discipline as every other port `ProcesarLote`
calls (AI_ENGINE_SPEC.md §2.5); the route boundary (`presentation/routes.py`) commits once, at
the very end.

**Known v1 limitation, not closed here (documented, not silently assumed away).** A suministro
that had a `resultados_ia` row from a PREVIOUS attempt of this SAME lote, but is no longer part of
`entradas` on a retry (e.g. it got newly excluded by Etapa 1 on a corrected reimport), keeps its
STALE `resultados_ia` row untouched -- this repository only ever upserts/refreshes rows for
suministros it IS given, it never soft-deletes a `resultados_ia` row for one it is NOT given (RD-023
treats `resultados_ia` as an upsert-only entity, and no directive calls for a "replace the whole
lote's resultados_ia set" semantics the way `predicciones`/`anomalias`/`impacto_economico` already
have). A future revision could clear it the same way `anomalias`/`impacto_economico` do, if this
edge case turns out to matter in practice.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.features import VERSION_FEATURES
from energia.contexts.motor.domain.ports import ResultadoIAParaGuardar

_UPSERT_RESULTADO_SQL = text(
    """
    INSERT INTO resultados_ia (
        suministro_id, lote_id, modelo_ia_id, prediccion_id, score_anomalia, probabilidad,
        clasificacion, observaciones
    )
    VALUES (
        :suministro_id, :lote_id, :modelo_ia_id, :prediccion_id, :score_anomalia, :probabilidad,
        :clasificacion, :observaciones
    )
    ON CONFLICT (suministro_id, lote_id) DO UPDATE SET
        modelo_ia_id = EXCLUDED.modelo_ia_id,
        prediccion_id = EXCLUDED.prediccion_id,
        score_anomalia = EXCLUDED.score_anomalia,
        probabilidad = EXCLUDED.probabilidad,
        clasificacion = EXCLUDED.clasificacion,
        observaciones = EXCLUDED.observaciones,
        fecha_analisis = now()
    RETURNING id
    """
)

_BACKFILL_FEATURE_VECTORS_SQL = text(
    """
    UPDATE feature_vectors
    SET resultado_ia_id = :resultado_ia_id
    WHERE suministro_id = :suministro_id AND lote_id = :lote_id AND version = :version
    """
)

_SOFT_DELETE_ANOMALIAS_SQL = text(
    """
    UPDATE anomalias
    SET deleted_at = now()
    WHERE deleted_at IS NULL
      AND resultado_ia_id IN (SELECT id FROM resultados_ia WHERE lote_id = :lote_id)
    """
)

_INSERT_ANOMALIA_SQL = text(
    """
    INSERT INTO anomalias (resultado_ia_id, tipo, severidad, descripcion)
    VALUES (:resultado_ia_id, :tipo, :severidad, :descripcion)
    """
)

_UPSERT_IRE_SQL = text(
    """
    INSERT INTO ire (resultado_ia_id, valor)
    VALUES (:resultado_ia_id, :valor)
    ON CONFLICT (resultado_ia_id) DO UPDATE SET
        valor = EXCLUDED.valor,
        fecha_calculo = now()
    """
)

_SOFT_DELETE_IMPACTO_ECONOMICO_SQL = text(
    """
    UPDATE impacto_economico
    SET deleted_at = now()
    WHERE deleted_at IS NULL
      AND resultado_ia_id IN (SELECT id FROM resultados_ia WHERE lote_id = :lote_id)
    """
)

_INSERT_IMPACTO_ECONOMICO_SQL = text(
    """
    INSERT INTO impacto_economico (resultado_ia_id, monto_estimado, moneda)
    VALUES (:resultado_ia_id, :monto_estimado, 'kWh')
    """
)


class SqlResultadosIaRepository:
    """`ResultadosIaRepository` (domain/ports.py) backed by the five statements the module
    docstring documents, all against the SAME session/transaction as the rest of the request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persistir(
        self, lote_id: UUID, entradas: Sequence[ResultadoIAParaGuardar]
    ) -> dict[UUID, UUID]:
        ids_por_suministro: dict[UUID, UUID] = {}
        for entrada in entradas:
            resultado = await self._session.execute(
                _UPSERT_RESULTADO_SQL,
                {
                    "suministro_id": entrada.suministro_id,
                    "lote_id": entrada.lote_id,
                    "modelo_ia_id": entrada.modelo_ia_id,
                    "prediccion_id": entrada.prediccion_id,
                    "score_anomalia": entrada.score_anomalia,
                    "probabilidad": entrada.probabilidad,
                    "clasificacion": entrada.clasificacion,
                    "observaciones": entrada.observaciones,
                },
            )
            ids_por_suministro[entrada.suministro_id] = resultado.scalar_one()

        if entradas:
            await self._session.execute(
                _BACKFILL_FEATURE_VECTORS_SQL,
                [
                    {
                        "resultado_ia_id": ids_por_suministro[entrada.suministro_id],
                        "suministro_id": entrada.suministro_id,
                        "lote_id": entrada.lote_id,
                        "version": VERSION_FEATURES,
                    }
                    for entrada in entradas
                ],
            )

        # `anomalias`/`impacto_economico`: soft-delete scoped to the WHOLE lote runs
        # UNCONDITIONALLY, even when `entradas` is empty -- mirrors `SqlPrediccionesRepository`'s
        # own "still clears stale rows on an empty retry" contract (module docstring).
        await self._session.execute(_SOFT_DELETE_ANOMALIAS_SQL, {"lote_id": lote_id})
        anomalias_parametros = [
            {
                "resultado_ia_id": ids_por_suministro[entrada.suministro_id],
                "tipo": anomalia.tipo,
                "severidad": anomalia.severidad,
                "descripcion": anomalia.descripcion,
            }
            for entrada in entradas
            for anomalia in entrada.anomalias
        ]
        if anomalias_parametros:
            await self._session.execute(_INSERT_ANOMALIA_SQL, anomalias_parametros)

        if entradas:
            await self._session.execute(
                _UPSERT_IRE_SQL,
                [
                    {
                        "resultado_ia_id": ids_por_suministro[entrada.suministro_id],
                        "valor": entrada.ire_valor,
                    }
                    for entrada in entradas
                ],
            )

        await self._session.execute(_SOFT_DELETE_IMPACTO_ECONOMICO_SQL, {"lote_id": lote_id})
        impacto_parametros = [
            {
                "resultado_ia_id": ids_por_suministro[entrada.suministro_id],
                "monto_estimado": entrada.iee_kwh,
            }
            for entrada in entradas
            if entrada.iee_kwh is not None
        ]
        if impacto_parametros:
            await self._session.execute(_INSERT_IMPACTO_ECONOMICO_SQL, impacto_parametros)

        return ids_por_suministro
