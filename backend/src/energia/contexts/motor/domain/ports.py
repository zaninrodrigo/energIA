"""Ports the `motor` application layer depends on (ADR-001 Dependency Inversion; ADR-006
modular-monolith cross-context lookups).

Both ports below are cross-context by construction: `Lote`/`Consumo`/`Lectura`/`Suministro`/
`CategoriaTarifaria` all belong to OTHER bounded contexts (`consumos`, `suministros` --
DOMAIN_MODEL.md §4.2/§4.3), not to `motor` (§4.4). Per `contexts/README.md`'s cross-context
directory-port pattern, `motor` never imports anything from `contexts.consumos`/
`contexts.suministros`; its infrastructure implementations run direct SQL (`sqlalchemy.text`)
against those tables instead -- the same sanctioned modular-monolith shortcut
`SqlDirectSuministroDirectory`/`SqlDirectClienteDirectory` already establish, extended here to a
port that also WRITES (`transicionar_estado`), not just resolves a natural key -- see
`contexts/README.md` ("El motor: puerto de escritura cruzada") for why that is a deliberate,
narrow extension of the pattern rather than a departure from it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from energia.contexts.motor.domain.lote_estado import EstadoLote

__all__ = [
    "EstadoLoteActual",
    "LoteProcesamientoPort",
    "ConsumoValidacionRow",
    "ValidacionDataSource",
    "PeriodoHistorialRow",
    "LecturaHistorialRow",
    "LoteConteoRow",
    "DuplicidadesDataSource",
    "FeatureConsumoRow",
    "SuministroMetadataRow",
    "PriorAnomalyCountRow",
    "FeaturesDataSource",
    "FeatureVectorParaGuardar",
    "FeatureVectorRepository",
    "IsolationForestScorer",
    "ModelosIaRepository",
    "PrediccionParaGuardar",
    "PrediccionesRepository",
    "AnomaliaParaGuardar",
    "ResultadoIAParaGuardar",
    "ResultadosIaRepository",
]


@dataclass(frozen=True, slots=True)
class EstadoLoteActual:
    """A snapshot of one lote's processing-relevant fields, as read by `LoteProcesamientoPort`."""

    id: UUID
    codigo_lote: str
    estado: EstadoLote
    cantidad_registros: int


class LoteProcesamientoPort(Protocol):
    """Cross-context port: read + optimistically write `lotes.estado`, without `motor` importing
    `contexts.consumos.domain.lote.Lote` or its repository. `Lote`'s own state machine
    (`ALLOWED_TRANSITIONS`) lives in `consumos/domain/lote.py`; `motor`'s mirror
    (`domain/lote_estado.py`) is the pre-flight check `ProcesarLote` runs before ever calling
    `transicionar_estado` here -- the SQL `WHERE estado IN (...)` in the implementation is the
    actual optimistic-concurrency guarantee, not just a repeat of that pre-flight check.
    """

    async def obtener_estado_actual(self, codigo_lote: str) -> EstadoLoteActual | None:
        """Return the (non-soft-deleted) lote's `id`/`estado`/`cantidad_registros` for this
        `codigo_lote`, or `None` if no such lote exists."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def contar_consumos_activos(self, lote_id: UUID) -> int:
        """Count non-soft-deleted `consumos` rows for this `lote_id` -- the completeness gate's
        second operand (AI_ENGINE_SPEC.md §2.1, `domain/completitud.py`)."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def transicionar_estado(
        self, lote_id: UUID, *, desde: frozenset[EstadoLote], hacia: EstadoLote
    ) -> bool:
        """Optimistically move `lote_id` from any state in `desde` to `hacia`:
        `UPDATE lotes SET estado = hacia WHERE id = lote_id AND estado IN (desde)`. Returns
        whether a row was actually updated -- `False` means a concurrent transition already
        moved this lote out of `desde` (the "concurrent trigger race degrades to 409" case,
        AI_ENGINE_SPEC.md §2.1). Does not commit: the caller (route boundary) controls the
        transaction.
        """
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class ConsumoValidacionRow:
    """One row of a lote's imported chain (`consumos` joined with `lecturas`/`suministros`/
    `categorias_tarifarias`), already shaped for the Etapa 1 checks (`domain/checks.py`) to
    evaluate directly -- no further query needed per check, per row.

    `lectura_anterior`/`lectura_actual`/`lectura_dias_facturados` are `None` whenever
    `lectura_id` is `None` (V1) OR the referenced lectura is no longer active -- both cases mean
    "no lectura data available for V2/V3", handled identically by those checks.

    `prev_fecha_inicio`/`prev_fecha_fin` are the immediately preceding period (by
    `fecha_inicio`) among ALL active consumos of this SAME suministro -- across every lote, not
    just this one (RD-017 is a suministro-level invariant, not a lote-scoped one; see
    `infrastructure/validacion_data_source.py`'s docstring for why the SQL window spans the
    suministro's full history). `None` when this is the first (or only) period on record for the
    suministro.

    `categoria_deleted_at` is the referenced `categorias_tarifarias.deleted_at` -- not `None`
    means the suministro's category has been soft-deleted, so it is no longer a valid cohort for
    comparison (RD-009, V6). `suministros.categoria_tarifaria_id` is `NOT NULL` at the schema
    level, so this -- not a missing FK -- is the only way V6 can actually fire in practice; see
    that check's docstring.
    """

    consumo_id: UUID
    suministro_id: UUID
    fecha_inicio: date
    fecha_fin: date
    dias_facturados: int
    kwh: Decimal
    lectura_id: UUID | None
    lectura_anterior: Decimal | None
    lectura_actual: Decimal | None
    lectura_dias_facturados: int | None
    prev_fecha_inicio: date | None
    prev_fecha_fin: date | None
    categoria_deleted_at: datetime | None


class ValidacionDataSource(Protocol):
    """Cross-context port: read the full imported chain (`consumos` + `lecturas` + `suministros`
    + `categorias_tarifarias`) a lote's Etapa 1 checks need, as one set-based query -- never a
    per-row loop of individual queries (see `infrastructure/validacion_data_source.py`).
    """

    async def fetch_chain(self, lote_id: UUID) -> Sequence[ConsumoValidacionRow]:
        """Return every active `consumos` row belonging to `lote_id`, already joined/enriched --
        see `ConsumoValidacionRow`'s docstring for exactly what each field means."""
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class PeriodoHistorialRow:
    """One row of a suministro's FULL active consumo-period history -- every lote that ever
    touched this suministro, not just the lote currently being processed (AI_ENGINE_SPEC.md §5,
    Etapa 2 / US-007). Input to `domain.duplicidades.detectar_periodos_conflictivos`.

    PLAIN per-suministro row -- no `LAG`/`prev_*` here (FIX 1, reviewer finding): a LAG-adjacency
    shape can only ever compare a row to its immediate predecessor by `fecha_inicio`, which misses
    overlaps between NON-adjacent periods (e.g. one long period overlapping two shorter, mutually
    disjoint ones only ever gets paired with the first neighbor under LAG; the second overlap is
    silently missed). `domain.duplicidades.detectar_periodos_conflictivos` instead sorts these by
    `fecha_inicio` per suministro and sweeps forward pairwise, enumerating EVERY overlapping pair
    -- see that function's docstring for the algorithm.
    """

    consumo_id: UUID
    suministro_id: UUID
    lote_id: UUID
    fecha_inicio: date
    fecha_fin: date


@dataclass(frozen=True, slots=True)
class LecturaHistorialRow:
    """One row of a suministro's full active `lecturas` history -- input to
    `domain.duplicidades.detectar_lecturas_near_duplicate` (AI_ENGINE_SPEC.md §5, Etapa 2 /
    US-007).

    PLAIN per-suministro row -- no `LAG`/`prev_*` here, for the same non-adjacency reason
    `PeriodoHistorialRow`'s docstring documents (FIX 1): an interleaved lectura with a DIFFERENT
    `lectura_actual` would otherwise break a LAG chain between two genuine near-duplicates a few
    days apart. `domain.duplicidades.detectar_lecturas_near_duplicate` instead sorts these by
    `fecha_lectura` per suministro and sweeps forward pairwise within the near-duplicate window --
    see that function's docstring for the algorithm.
    """

    lectura_id: UUID
    suministro_id: UUID
    fecha_lectura: date
    lectura_actual: Decimal


@dataclass(frozen=True, slots=True)
class LoteConteoRow:
    """One OTHER lote (not the one being processed) that shares at least one suministro with the
    lote being processed, with its declared `cantidad_registros` vs. its CURRENT active `consumos`
    count -- input to `domain.duplicidades.detectar_drift_lotes` (AI_ENGINE_SPEC.md §5's amended
    "consumo repetido entre lotes" row: the natural-key upsert (`consumos.
    uq_consumos_suministro_periodo`) migrates a re-imported period's `lote_id` to whichever lote
    re-imported it, so the detectable residue of a cross-lote repeat is this count drifting away
    from what the OTHER lote itself declared, not a duplicate row surviving under two lotes at
    once -- see `infrastructure/duplicidades_data_source.py` for the query.
    """

    lote_id: UUID
    codigo_lote: str
    cantidad_registros: int
    consumos_activos: int


class DuplicidadesDataSource(Protocol):
    """Cross-context port: Etapa 2's set-based reads (AI_ENGINE_SPEC.md §5, US-007) -- full
    suministro-level history (periods + lecturas) and other-lote consumo counts, all scoped by
    the lote currently being processed (see `infrastructure/duplicidades_data_source.py`).
    """

    async def fetch_historial_periodos(self, lote_id: UUID) -> Sequence[PeriodoHistorialRow]:
        """Return the FULL active consumo-period history (every lote, ordered by `suministro_id`,
        `fecha_inicio`) of every suministro that has at least one active consumo in `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def fetch_historial_lecturas(self, lote_id: UUID) -> Sequence[LecturaHistorialRow]:
        """Return the full active `lecturas` history (ordered by `suministro_id`,
        `fecha_lectura`) of every suministro that has at least one active consumo in `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def fetch_conteos_lotes_relacionados(self, lote_id: UUID) -> Sequence[LoteConteoRow]:
        """Return every OTHER lote (`lote_id` excluded) that shares at least one suministro with
        `lote_id`, each with its `cantidad_registros` vs. its current active `consumos` count."""
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class FeatureConsumoRow:
    """One row of a suministro's FULL active consumo history (every lote, every period ever
    billed) -- input to `domain.features.construir_feature_vector` (AI_ENGINE_SPEC.md §6, Etapa
    3 / US-008). Unlike `PeriodoHistorialRow` (Etapa 2), this carries `kwh`/`dias_facturados`:
    Etapa 3's windows need the actual consumption values, not just the period boundaries.

    Conflicted-period exclusion (DEC-005, AI_ENGINE_SPEC.md §6 note) happens BEFORE this row
    ever reaches the domain layer: `ProcesarLote._generar_features` (application layer) drops
    any row whose `consumo_id` appears in Etapa 2's `periodos_conflictivos` (both sides of each
    pair) before grouping rows by `suministro_id` -- `construir_feature_vector` itself has no
    notion of "conflicted" at all for F1-F9/F11-F17, it simply reflects whatever history it is
    given (see that function's docstring and
    `tests/unit/contexts/motor/domain/test_features.py`'s
    `test_conflicted_period_exclusion_is_a_prior_filtering_concern`). **F10
    `zero_consumption_streak` is the one exception** (FIX 1, reviewer finding, CRITICAL):
    `_generar_features` ALSO builds the unfiltered grouping (conflicted rows included) and passes
    it as `construir_feature_vector`'s `historial_suministro_completo`, used only for F10 -- see
    that function's docstring for why a conflicted-but-nonzero period must still break a
    zero-streak.
    """

    consumo_id: UUID
    suministro_id: UUID
    lote_id: UUID
    fecha_inicio: date
    fecha_fin: date
    dias_facturados: int
    kwh: Decimal


@dataclass(frozen=True, slots=True)
class SuministroMetadataRow:
    """One suministro's Etapa 3 metadata (categoria/localidad/fecha_alta) -- everything
    `construir_feature_vector` needs besides the kwh history itself (F14 `supply_age_days`, F17
    `categoria_tarifaria`, and the cohort grouping key `categoria_tarifaria_id` x `localidad`,
    DEC-008).

    `numero_suministro`/`estado` were added for Etapa 5 (AI_ENGINE_SPEC.md §8, US-0xx reglas de
    negocio): `estado` is R1's "suministro activo" condition (`suministros.estado`, no enumerated
    value list at the DDL level -- exact string match against the seeded default `'Activo'`,
    `domain/reglas.py`'s `ESTADO_SUMINISTRO_ACTIVO`); `numero_suministro` is the natural key the
    Etapa 5 informe reports hits against (more actionable for an inspector than a bare UUID).

    `categoria_tarifaria_nombre` was added for Etapa 6 (AI_ENGINE_SPEC.md §9, DEC-010):
    `domain/isolation_forest.py`'s `agrupar_para_entrenamiento` needs the categoria's human-
    readable `nombre` (e.g. `"Residencial"`) to build `modelos_ia.nombre`
    (`isolation-forest-{scope}`) -- `FeatureVector` itself only ever carries the UUID
    (`categoria_tarifaria_id`; F17 `categoria_tarifaria` in the jsonb is `str(categoria_tarifaria_
    id)`, not the name), so this is the one place that name is available to the application
    layer without a separate round trip."""

    suministro_id: UUID
    categoria_tarifaria_id: UUID
    localidad: str | None
    fecha_alta: date
    numero_suministro: str
    estado: str
    categoria_tarifaria_nombre: str


@dataclass(frozen=True, slots=True)
class PriorAnomalyCountRow:
    """One suministro's historical anomaly count -- F15 `prior_anomaly_count`. Always `0` today
    (correct by construction: `anomalias`/`resultados_ia` stay empty until Etapas 6-7 land and
    start writing them -- see `domain/features.py`'s module docstring)."""

    suministro_id: UUID
    cantidad: int


class FeaturesDataSource(Protocol):
    """Cross-context port: Etapa 3's set-based reads (AI_ENGINE_SPEC.md §6, US-008) -- full
    suministro-level kwh history, suministro metadata, and prior anomaly counts, all scoped by
    the lote currently being processed (see `infrastructure/features_data_source.py`). One query
    per concern (RNF-001), never a per-suministro loop.
    """

    async def fetch_historial_kwh(self, lote_id: UUID) -> Sequence[FeatureConsumoRow]:
        """Return the FULL active consumo history (every lote, ordered by `suministro_id`,
        `fecha_inicio`) of every suministro that has at least one active consumo in `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def fetch_metadata_suministros(self, lote_id: UUID) -> Sequence[SuministroMetadataRow]:
        """Return the categoria/localidad/fecha_alta of every suministro that has at least one
        active consumo in `lote_id`."""
        ...  # pragma: no cover — Protocol stub, never executed directly

    async def fetch_prior_anomaly_counts(self, lote_id: UUID) -> Sequence[PriorAnomalyCountRow]:
        """Return the historical `anomalias` count (GROUP BY suministro_id) of every suministro
        that has at least one active consumo in `lote_id`. A suministro with no matching row has
        `0` prior anomalies -- the caller defaults missing entries to `0`, this method never
        returns a zero-count row explicitly (an empty GROUP BY result omits it entirely)."""
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class FeatureVectorParaGuardar:
    """The minimal shape `FeatureVectorRepository.guardar_batch` persists -- deliberately NOT
    `domain.features.FeatureVector` itself (which also carries transient, non-persisted cohort-
    grouping fields like `localidad`/`kwh_actual`, see that module's docstring): keeping this
    port's own shape here (not importing `domain.features`) avoids a circular import (`domain.
    features` already imports `FeatureConsumoRow` etc. from this module)."""

    suministro_id: UUID
    lote_id: UUID
    features: dict[str, object]


class FeatureVectorRepository(Protocol):
    """Same-context port: `feature_vectors` is the first table Etapas 3-4 actually WRITE that
    belongs to `motor`'s own bounded context (DOMAIN_MODEL.md §8.5), unlike the cross-context
    `lotes`/`consumos` reads/writes above -- see `infrastructure/feature_vector_repository.py`'s
    docstring for why this is still implemented with raw SQL rather than an ORM model.
    """

    async def guardar_batch(self, vectores: Sequence[FeatureVectorParaGuardar]) -> None:
        """Upsert every vector: `INSERT ... ON CONFLICT (suministro_id, lote_id, version) DO
        UPDATE` -- a reprocess (Error -> retry) overwrites the same rows instead of duplicating
        them (mission directive #6). Does not commit: same session/transaction discipline as
        every other port in this module."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class IsolationForestScorer(Protocol):
    """Etapa 6 (AI_ENGINE_SPEC.md §9, DEC-011/DEC-012): fit `RobustScaler` + `IsolationForest`
    over ONE training group's already-built numeric matrix (`domain/isolation_forest.py`'s
    `construir_matriz`) and return the RAW `decision_function` score per row, in the SAME order
    as the input matrix's rows. This is the ONE non-pure piece of Etapa 6 (`domain/
    isolation_forest.py`'s module docstring) -- sklearn is a third-party ML library, an adapter,
    exactly like `FeatureVectorRepository` is an adapter around SQL. See
    `infrastructure/isolation_forest_scorer.py`'s docstring for the `asyncio.to_thread` wrapping
    (AI_ENGINE_SPEC.md §2.4/§12: CPU-bound, must never block the event loop)."""

    async def ajustar_y_puntuar(self, matriz: Sequence[Sequence[float]]) -> Sequence[float]:
        """Fit on `matriz` (rows = suministros, columns = `NUMERIC_FEATURE_KEYS`) and return one
        RAW `decision_function` score per row -- more negative = more anomalous. Never
        normalized/banded here (`domain/isolation_forest.py` owns that, DEC-013/DEC-015)."""
        ...  # pragma: no cover — Protocol stub, never executed directly


class ModelosIaRepository(Protocol):
    """Same-context port: `modelos_ia` (DOMAIN_MODEL.md §8.6/§10.5) -- Etapa 6's model registry
    write. See `infrastructure/modelos_ia_repository.py`'s docstring for the upsert + single-
    Activo-per-nombre semantics, and for why `registrar_fit` takes a transaction-scoped advisory
    lock keyed on `nombre` (serializes concurrent fits of the SAME scope across different lotes,
    fixing a race where two such fits could each commit their own `Activo` row)."""

    async def registrar_fit(self, *, nombre: str, version: str, algoritmo: str) -> UUID:
        """Register one scope's fit for this lote: upsert `(nombre, version)` as `Activo`
        (idempotent across an Error-retry reprocess of the SAME lote, since `domain/
        isolation_forest.py`'s `construir_version_modelo` is deterministic per `codigo_lote`),
        then flip every OTHER row sharing `nombre` that is currently `Activo` to `Obsoleto`.
        Returns the (possibly pre-existing) row's `id`. Does not commit: same session/transaction
        discipline as every other port in this module."""
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class PrediccionParaGuardar:
    """The minimal shape `PrediccionesRepository.reemplazar_lote` persists -- deliberately NOT
    `domain.isolation_forest.PrediccionSuministro` itself (which also carries `numero_suministro`/
    `scope`, transient reporting-only fields not stored on `predicciones`), mirroring
    `FeatureVectorParaGuardar`'s own reasoning above.

    `id` (added for Etapa 7, AI_ENGINE_SPEC.md §14) is CLIENT-generated (`uuid4()`, `domain.
    isolation_forest.PrediccionSuministro`'s own construction site) rather than left to the
    column's `gen_random_uuid()` default: Etapa 7's `resultados_ia.prediccion_id` FK needs to
    know exactly which `predicciones` row belongs to which suministro, in the SAME `ProcesarLote.
    execute()` run, WITHOUT a second round trip to read it back -- a multi-row `INSERT ...
    VALUES (...), (...) ... RETURNING id` cannot be safely correlated back to its own input rows
    by position when issued as a driver-level executemany (no ORDER BY guarantee across drivers),
    so this repository writes the id it was given instead of asking Postgres to generate one."""

    id: UUID
    modelo_ia_id: UUID
    suministro_id: UUID
    lote_id: UUID
    score: float
    clasificacion: str


class PrediccionesRepository(Protocol):
    """Same-context port: `predicciones` (DOMAIN_MODEL.md §8.7) -- Etapa 6's per-suministro
    scoring write. See `infrastructure/predicciones_repository.py`'s docstring for why this is a
    soft-delete-then-insert, not an `ON CONFLICT` upsert (unlike `FeatureVectorRepository`/
    `ModelosIaRepository`): `predicciones` has no natural unique key beyond its surrogate `id`
    (`docker/postgres/init/01_schema.sql`), and a physical `DELETE` would violate
    DATABASE_DESIGN.md §10 ("ningún DELETE físico") and risk a future FK violation once Etapa 7's
    `resultados_ia.prediccion_id` references it (`infrastructure/predicciones_repository.py`'s
    module docstring)."""

    async def reemplazar_lote(self, lote_id: UUID, filas: Sequence[PrediccionParaGuardar]) -> None:
        """Idempotent Error-retry reprocess (mission directive #6): soft-delete every existing
        `predicciones` row for `lote_id` FIRST (`deleted_at = now()`, never a physical `DELETE`),
        then bulk-insert `filas` -- a retry of the SAME lote never accumulates duplicate ACTIVE
        rows from a previous attempt, and the previous attempt's rows stay FK-safe for anything
        that already references them. Does not commit: same session/transaction discipline as
        every other port in this module."""
        ...  # pragma: no cover — Protocol stub, never executed directly


@dataclass(frozen=True, slots=True)
class AnomaliaParaGuardar:
    """One `anomalias` row to persist for a suministro's `ResultadoIA` (Etapa 7, §14) --
    `tipo`/`severidad` are the EXACT literals `ck_anomalias_tipo`/`ck_anomalias_severidad`
    (`docker/postgres/init/01_schema.sql`) list, already validated by `domain/reglas.py`'s
    `ReglaHit`/`domain/ire.py`'s ML-only anomaly policy (`debe_generar_anomalia_ml`) before this
    dataclass is ever constructed -- this port performs no validation of its own."""

    tipo: str
    severidad: str
    descripcion: str | None


@dataclass(frozen=True, slots=True)
class ResultadoIAParaGuardar:
    """One suministro's COMPLETE Etapa 7-8 write (the "convergence", AI_ENGINE_SPEC.md §14) --
    everything `ResultadosIaRepository.persistir` needs to upsert `resultados_ia`, backfill
    `feature_vectors.resultado_ia_id`, replace `anomalias`, upsert `ire` and upsert/soft-replace
    `impacto_economico`, all for ONE suministro, in the SAME transaction as every other write.

    `observaciones` is DEC-016's serialized JSON breakdown (`domain/ire.py`'s
    `FactorContribucion` list, `json.dumps` by the caller -- this port stores the string verbatim,
    it does not know the JSON's shape). `ire_valor` is the ALREADY-ROUNDED integer (`domain/
    ire.py`'s `redondear_half_up`). `iee_kwh` is `None` for a cold-start suministro (no IEE
    computed, `domain/ire.py`'s `calcular_iee_kwh`) -- `None` means "write no `impacto_economico`
    row for this suministro" (and soft-delete/clear any stale one from a previous attempt), never
    a fabricated `0.0`."""

    suministro_id: UUID
    lote_id: UUID
    modelo_ia_id: UUID
    prediccion_id: UUID
    score_anomalia: float
    probabilidad: float
    clasificacion: str
    observaciones: str
    ire_valor: int
    iee_kwh: float | None
    anomalias: tuple[AnomaliaParaGuardar, ...]


class ResultadosIaRepository(Protocol):
    """Same-context port: THE Etapa 7-8 convergence write (AI_ENGINE_SPEC.md §14) -- `resultados_
    ia`, `feature_vectors.resultado_ia_id` (backfill), `anomalias`, `ire`, `impacto_economico`, all
    for one lote's analyzed suministros, in ONE call. See `infrastructure/
    resultados_ia_repository.py`'s docstring for the exact per-table write strategy (upsert vs.
    soft-delete-then-insert) and the §14 write order this implementation follows."""

    async def persistir(
        self, lote_id: UUID, entradas: Sequence[ResultadoIAParaGuardar]
    ) -> dict[UUID, UUID]:
        """Persist `entradas` (one per analyzed suministro this lote) and return the `suministro_
        id -> resultados_ia.id` mapping this run produced/refreshed (idempotent Error-retry
        reprocess, mission directive #6: an upsert by `(suministro_id, lote_id)` reuses the SAME
        `resultados_ia.id` across a retry, never duplicating it). Does not commit: same session/
        transaction discipline as every other port in this module."""
        ...  # pragma: no cover — Protocol stub, never executed directly
