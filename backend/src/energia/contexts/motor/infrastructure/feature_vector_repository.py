"""SqlFeatureVectorRepository: `FeatureVectorRepository`'s implementation (Etapa 3, US-008,
AI_ENGINE_SPEC.md §6) -- the first WRITE `motor` performs against a table it actually owns
(`feature_vectors`, DOMAIN_MODEL.md §8.5), unlike the cross-context `lotes`/`consumos` writes/
reads elsewhere in this context (`domain/ports.py`'s module docstring).

**Why this is still raw SQL, not an ORM-backed repository.** `contexts/README.md`'s default
pattern for a same-context entity is an ordinary ORM repository (a mapped model in this
context's own `infrastructure/models.py`) -- `motor` has none today, because every prior write
(`lotes.estado`) and read (`consumos`/`suministros`/`anomalias`) crossed a context boundary and
used raw SQL for that reason. This repository keeps that same raw-SQL style for two concrete
reasons, not just inertia: (1) the idempotent-reprocess requirement (mission directive #6 --
`Error -> Procesando` retry must upsert, never duplicate, `feature_vectors` rows) is most
directly expressed as a single set-based `INSERT ... ON CONFLICT (suministro_id, lote_id,
version) DO UPDATE` statement, which SQLAlchemy's ORM session (`Session.merge()`/bulk helpers)
does not cleanly express as native upsert semantics without dropping to Core anyway; (2) adding
a `models.py` file with a single mapped model, only to route ONE query through it, is more net-
new ceremony than the raw-SQL alternative for a context whose only own-table write today is this
one batch upsert. This is a pragmatic/consistency trade-off, not a load-bearing architectural
decision -- a future `FeatureVectorModel` ORM refactor would not need to change this port's
abstract contract (`domain/ports.py`'s `FeatureVectorRepository`), since callers never see the
raw-SQL choice.
"""

import json
from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from energia.contexts.motor.domain.features import VERSION_FEATURES
from energia.contexts.motor.domain.ports import FeatureVectorParaGuardar

_UPSERT_SQL = text(
    """
    INSERT INTO feature_vectors (suministro_id, lote_id, version, features)
    VALUES (:suministro_id, :lote_id, :version, CAST(:features AS jsonb))
    ON CONFLICT (suministro_id, lote_id, version)
    DO UPDATE SET features = EXCLUDED.features, fecha_generacion = now()
    """
)


class SqlFeatureVectorRepository:
    """`FeatureVectorRepository` (domain/ports.py) backed by a single batched upsert statement,
    same session/transaction as the rest of the request."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def guardar_batch(self, vectores: Sequence[FeatureVectorParaGuardar]) -> None:
        """No-ops on an empty batch (a lote with zero non-excluded suministros, e.g. every
        suministro failed Etapa 1) -- `session.execute` with an empty parameter list would be a
        wasted round trip for nothing to write."""
        if not vectores:
            return

        parametros = [
            {
                "suministro_id": vector.suministro_id,
                "lote_id": vector.lote_id,
                "version": VERSION_FEATURES,
                "features": json.dumps(vector.features),
            }
            for vector in vectores
        ]
        await self._session.execute(_UPSERT_SQL, parametros)
