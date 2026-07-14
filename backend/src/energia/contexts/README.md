# Bounded contexts

Per ADR-001 (Clean Architecture + tactical DDD) and ADR-006 (modular monolith), each bounded
context from `docs/03-architecture/DOMAIN_MODEL.md` §4 becomes a Python package under
`contexts/`, not a separate service or deployment. Module boundaries — not network
boundaries — are what the runtime enforces today; extracting a context into its own service
later remains possible without a redesign (ADR-006).

## The 7 bounded contexts

| Package (code identifier) | Bounded context (`DOMAIN_MODEL.md` §4, canonical name) | Status |
|---|---|---|
| `clientes` | Gestión de Clientes | **Implemented** (US-001) |
| `suministros` | Gestión de Suministros | **Implemented** (US-002) |
| `consumos` | Gestión de Consumos | **Implemented, all 3 entities** (US-003 `Lectura`, US-005 `Lote de Facturación`, US-004 `Consumo` — Épica 1 complete) |
| `motor` | Motor de Inteligencia Energética | **Implemented, Etapa 1** (US-006 + US-010 trigger — Épica 2 slice 1; Etapas 2-8 not started) |
| `risk` | Gestión del Riesgo | Not started |
| `inspections` | Gestión de Inspecciones | Not started |
| `dashboard` | Dashboard Ejecutivo | Not started |

`clientes` is the first context implemented and sets the two conventions below. `suministros`
(§4.2) is the second — note it is the Spanish domain noun itself (per the naming convention),
superseding the `supplies` placeholder this table used before that context shipped. `consumos`
(§4.3) is the third, superseding the `consumption` placeholder the same way — see "One package,
staged entities" below for why it ships today with only `Lectura` implemented, ahead of
`Consumo`/`Lote de Facturación`. `motor` (§4.4) is the fourth, superseding the `intelligence_engine`
placeholder the same way — see "The `motor` context" below for its naming and the new
cross-context write pattern it establishes. Package names for contexts not yet started are still
placeholders (English, one option among several); the `clientes`/`suministros`/`consumos`/`motor`
naming convention takes precedence once each context actually lands — see below.

## Naming convention: Spanish domain nouns, English technical parts

Established by the `clientes` context (US-001) and binding for every context from here on:

- **Domain-concept nouns keep their ubiquitous-language Spanish name**, exactly as written in
  `DOMAIN_MODEL.md` and the DB schema (`docker/postgres/init/01_schema.sql`) — e.g. `Cliente`,
  `numero_cliente`, `estado`. This is also why the context package itself is `clientes`, not
  `customers`: the package name denotes the same domain noun DOMAIN_MODEL.md §4.1 uses
  ("Gestión de **Clientes**"), so it stays Spanish too, superseding the `customers` placeholder
  this table used before any context had shipped.
- **Technical parts stay English**: verbs, suffixes, and infrastructure — e.g.
  `ClienteRepository`, `import_clientes`, `execute`, `save`, `ImportSummary`.
- Prose documentation (docstrings, this file, ADRs) is written in neutral, professional
  Spanish or English depending on the document's own existing convention; code identifiers
  follow the rule above regardless of which language the surrounding prose uses.

## Source-port pattern for imports

`clientes` establishes the pattern every future *import* use case in this codebase should
follow, driven by US-001's constraint: EnergIA has no Oracle access yet (ADR-004 — Oracle is a
read-only source of consumption history, reached via ETL, someday), so `ImportClientes`
(`application/import_clientes.py`) cannot depend on Oracle directly.

Instead, the use case depends on a **source port** — `ClienteSource`
(`domain/ports.py`), a `Protocol` with a single `fetch()` method yielding raw, not-yet-validated
records. Today's only adapter is `JsonClienteSource`
(`infrastructure/json_cliente_source.py`), which wraps the HTTP request payload (a JSON array)
already parsed by the presentation layer. A file adapter (CSV/Excel upload) and, eventually,
the real Oracle ETL adapter can implement the exact same `ClienteSource` port later — the use
case, and everything above it, does not change.

The same shape applies to persistence: `ClienteRepository` (`domain/ports.py`) is the only
storage port the application layer sees; `SqlAlchemyClienteRepository`
(`infrastructure/cliente_repository.py`) is today's only implementation.

## Cross-context directory-port pattern

`suministros` (US-002) establishes the pattern every future use case that needs data owned by
*another* bounded context should follow, driven by a constraint ADR-006 (modular monolith) makes
explicit: module boundaries are enforced by discipline and code review, not by the runtime (a
network boundary would enforce them "for free", but this project deliberately does not pay that
operational cost — see ADR-006's accepted trade-offs). Concretely: `ImportSuministros`
(`suministros/application/import_suministros.py`) needs to resolve a `numero_cliente` natural
key to the `cliente_id` UUID `suministros.cliente_id`'s foreign key requires, but `Cliente` and
its table belong to `clientes`, a different bounded context (`DOMAIN_MODEL.md` §4.1 vs §4.2) —
importing `contexts.clientes.infrastructure.cliente_repository.SqlAlchemyClienteRepository` (or
anything else from `contexts.clientes`) directly would violate ADR-001's module boundaries, even
though both contexts share the exact same physical database.

The resolution is a **directory port** — `ClienteDirectory`
(`suministros/domain/ports.py`), a `Protocol` with a single `resolve(natural_key) -> id | None`
method. Its implementation, `SqlDirectClienteDirectory`
(`suministros/infrastructure/cliente_directory.py`), runs a direct, explicit SQL query
(`sqlalchemy.text(...)`, not the ORM) against the `clientes` table — the sanctioned
modular-monolith shortcut ADR-006 allows precisely because both contexts share one database:
same connection, same transaction, no network call, but still routed through an explicit,
narrow interface instead of reaching into `clientes`' own domain/application/infrastructure code.
Deliberately raw SQL rather than a duplicate ORM mapping of `clientes`' table: a shadow model
would double the places that table's shape is declared and invite `suministros` to depend on
`clientes`' column set evolving in lockstep, exactly the coupling the port exists to prevent.

**When a directory port is *not* needed**: not every entity referenced by another context's
import needs one. `categoria_tarifaria` is also a natural-key reference `ImportSuministros` must
resolve to a UUID, but `CategoriaTarifaria` belongs to the *same* bounded context as `Suministro`
(`DOMAIN_MODEL.md` §4.2, "Gestión de Suministros") — so its resolution port,
`CategoriaTarifariaDirectory`, is implemented with an ordinary ORM query
(`SqlAlchemyCategoriaTarifariaDirectory`, `suministros/infrastructure/
categoria_tarifaria_directory.py`) against a `CategoriaTarifariaModel` mapped in this context's
own `infrastructure/models.py`, the same way `SuministroRepository` queries `suministros`. It is
still its own small port (not folded into `SuministroRepository`) purely so `ImportSuministros`
stays unit-testable against a plain fake — not because of any cross-context concern.

`ImportConsumos` (US-004) follows the exact same same-context rule for `codigo_lote` ->
`lote_id`: `Lote` and `Consumo` both belong to `consumos`, so `LoteDirectory`
(`consumos/domain/ports.py`) / `SqlAlchemyLoteDirectory`
(`consumos/infrastructure/lote_directory.py`) is an ordinary ORM query against `LoteModel`
(already mapped in this context's own `infrastructure/models.py`), not a raw-SQL cross-context
lookup like `SuministroDirectory`. `ImportConsumos` needs *both* kinds of resolution at once:
`numero_suministro` -> `suministro_id` via the cross-context `SuministroDirectory`, and
`codigo_lote` -> `lote_id` via the same-context `LoteDirectory` — the first entity in this
codebase that resolves one natural key of each kind before a single `create()` call.

Future contexts needing another context's data (e.g. `inspections` resolving a `resultado_ia_id`)
should follow the same rule: if the referenced entity belongs to a *different* bounded context,
define a `<Entity>Directory` port resolved by a direct SQL query in infrastructure; if it belongs
to the *same* context, an ordinary same-context repository/ORM query is enough — no port-naming
ceremony required beyond what unit-testability already asks for.

`consumos` (US-003) is the first context that actually follows this rule for a second time:
`ImportLecturas` (`consumos/application/import_lecturas.py`) resolves a `numero_suministro`
natural key to the `suministro_id` UUID `lecturas.suministro_id`'s foreign key requires, via
`SuministroDirectory` (`consumos/domain/ports.py`) / `SqlDirectSuministroDirectory`
(`consumos/infrastructure/suministro_directory.py`) — the exact same shape as `ClienteDirectory`/
`SqlDirectClienteDirectory`, resolving *active* (non-soft-deleted) suministros only.

## Internal shape of a context

Once a context exists, it follows the same four Clean Architecture layers as the rest of the
backend (ADR-001):

```
contexts/<context_name>/
  domain/           # entities, value objects, aggregates, domain services, repository interfaces
  application/       # use cases orchestrating the domain
  infrastructure/     # repository implementations, ORM models, external integrations
  presentation/        # FastAPI routers and DTOs for this context
```

`dashboard` is the documented exception (ADR-001, "Negativas"): `DOMAIN_MODEL.md` §4.7 states
it "no contiene reglas de negocio" — it is read-only. That context is expected to use light,
CQRS-style query services instead of the full entity/aggregate/use-case apparatus, per the
mitigation ADR-001 records for over-engineering read-only contexts.

## Known ambiguity: `direccion`'s type

`DOMAIN_MODEL.md` §7.1 documents `direccion` (Cliente's Atributos table) as type `String`, but
the executable schema (`docker/postgres/init/01_schema.sql`, table `clientes`) defines it as
`jsonb`. This is a genuine, unresolved discrepancy between the domain doc and the DB schema —
not a typo one of the two documents can simply absorb, since `String` and `jsonb` imply
different validation and query capabilities.

The implementation (`domain/cliente.py`, `infrastructure/models.py`) follows the DB schema: it
treats `direccion` as `dict[str, Any] | None`, an open passthrough JSON object with no fixed
shape beyond the size cap `Cliente.create()` enforces (8 KB serialized). No structured
sub-fields (`calle`, `numero`, etc.) are validated or required — the domain simply stores
whatever JSON object the source provides. Reconciling `DOMAIN_MODEL.md` §7.1 to match (or
deciding a structured shape is actually wanted) is open; see `docs/03-architecture/API_SPEC.md`
("Contexto: Gestión de Clientes") for the field as documented today.

## Comportamiento ante soft-delete

**DECISION #9 (resurrection, confirmed by business — Rodrigo Zanin, 2026-07-13 — see
`PROJECT_MASTER_SPEC.md`, resolved items):** re-importing a natural key whose row is soft-deleted
(`deleted_at IS NOT NULL`) **revives that same row** — same `id`, `deleted_at` cleared, fields
merged from the re-import — instead of creating a brand-new identity. The rationale: a natural
key (`numero_cliente`, `numero_suministro`, `codigo_lote`, or a composite key like
`(suministro_id, fecha_lectura)`) denotes a single identity across its *whole* lifecycle,
soft-delete included, not a fresh one every time it happens to come back. This replaces the
previous (2026-07-13-and-earlier) documented behavior — "soft-delete re-import creates a new
identity" — which was this codebase's initial, deliberate-but-unreviewed default until business
weighed in.

Mechanically, each import use case (`ImportClientes`, `ImportSuministros`, `ImportLecturas`,
`ImportLotes`, `ImportConsumos`) now runs a second lookup whenever the first (`get_by_<natural
key>`, scoped to `deleted_at IS NULL`) finds nothing: `get_most_recently_deleted_by_<natural
key>` (the same repository, ordered by `deleted_at DESC`, so only the *most recently* deleted row
is ever a candidate — older dead rows sharing the same key, however they arose, stay dead). A hit
there resurrects instead of creating: `repository.resurrect(entity)` clears `deleted_at` and
writes every mutable field on that SAME row, using each entity's normal update-merge (Cliente/
Suministro/Lectura re-validate through `create()`; Lote applies its `UNSET`-aware `nombre`/
`cantidad_registros` merge while keeping `estado`/`fecha_importacion` untouched, exactly like an
ordinary update; Consumo recomputes `consumo_promedio_diario` fresh, exactly like an ordinary
update). `resurrect()` is a plain `UPDATE ... WHERE id = :id`, not `save()`'s `INSERT ... ON
CONFLICT` upsert: a dead row is excluded from its natural key's partial unique index (`WHERE
deleted_at IS NULL`), so that upsert's `ON CONFLICT` target could never match it in the first
place — inserting the same `id` again would only collide with the primary key instead. Each
`ImportSummary` gained a `restored` count, separate from `created`/`updated`/`unchanged`, so a
resurrection is visible in the API response (`docs/03-architecture/API_SPEC.md`).

A resurrection always writes, even when every field already matches the dead row's stored
values — `deleted_at` itself is what's changing — so it is never folded into `unchanged`. Once
resurrected, the row is active again: a further identical re-import finds it through the
ordinary active-row lookup, not the dead-row path, and reports `unchanged` like any other
already-current row.

Because resurrection keeps the *same* `id`, any historical FK pointing at it (e.g.
`suministros.cliente_id`) now keeps resolving correctly across a soft-delete/resurrection cycle —
tested by `test_resurrecting_a_suministro_does_not_disturb_a_historical_fk_to_its_cliente`
(`tests/integration/contexts/suministros/test_suministros_routes_integration.py`). This is the
direct benefit over the old "new identity" behavior, where a historical FK kept pointing at the
old, permanently soft-deleted row.

Tested per context: `test_reimporting_a_soft_deleted_numero_cliente_resurrects_the_original_row`
(`tests/integration/contexts/clientes/test_clientes_routes_integration.py`, plus multiple-dead-
rows and resurrected-then-unchanged variants alongside it),
`test_reimporting_a_soft_deleted_numero_suministro_resurrects_the_original_row`
(`tests/integration/contexts/suministros/test_suministros_routes_integration.py`),
`test_reimporting_a_soft_deleted_key_resurrects_the_original_row`
(`tests/integration/contexts/consumos/test_lecturas_routes_integration.py`),
`test_reimporting_a_soft_deleted_codigo_lote_resurrects_the_original_row` plus
`test_resurrecting_a_lote_that_transitioned_to_procesado_keeps_that_estado`
(`tests/integration/contexts/consumos/test_lotes_routes_integration.py`), and
`test_reimporting_a_soft_deleted_consumo_resurrects_the_original_row`
(`tests/integration/contexts/consumos/test_consumos_routes_integration.py`) — each mirrored by
unit tests against the in-memory fakes and by repository-level integration tests for
`get_most_recently_deleted_by_<natural key>`/`resurrect()`, covering two independent races, both
degrading to a per-record rejection rather than corrupting anything: (1) the natural-key race —
a dead row's own natural key gets claimed by a concurrent active insert before its resurrection
commits, caught by the natural key's own partial unique index and surfaced as
`<Entity>ConflictError`; and (2) the same-dead-row race — two concurrent resurrections of the
SAME dead row, where neither `UPDATE` would otherwise conflict with anything (same row, same
natural key), so without an explicit guard both silently "succeed" and whichever commits last
wins with no error at all (a lost-update, not a corruption of the partial unique index, but a
silent one nonetheless). `resurrect()`'s `WHERE` clause requires `deleted_at IS NOT NULL` in
addition to matching identity, and checks `result.rowcount`: a concurrent resurrection that
already cleared this row's `deleted_at` makes the loser's `UPDATE` match zero rows, raised as
`<Entity>ConflictError` — the same per-record-rejection outcome as race (1), not a silent
overwrite. `get_most_recently_deleted_by_<natural key>` also breaks `deleted_at` ties
deterministically (`ORDER BY deleted_at DESC, id DESC` — `id` is arbitrary-but-stable, carrying no
business meaning of its own) so which dead row is picked never depends on Postgres's unspecified
scan order when two dead rows share the exact same `deleted_at`.

**FK race with a soft-deleted `cliente` — now healable, not just narrow.** `fk_suministros_cliente`
(`docker/postgres/init/01_schema.sql`) is an ordinary foreign key against `clientes.id` — it does
not, and cannot, check `deleted_at`. If a `cliente` were soft-deleted *between* `ClienteDirectory`
resolving its `id` and `ImportSuministros`' `INSERT`/`UPDATE` of the `suministro` row, the new or
updated `suministro` would end up referencing a now soft-deleted `cliente`. This is currently
unreachable in practice — the API exposes no endpoint to delete/deactivate a `cliente` at all —
but the resurrection this section describes changes what happens if it ever is reached: a stale
reference like that is no longer permanent. Re-importing the same `numero_cliente` resurrects the
SAME `cliente` row (same `id`), so the `suministro`'s FK — which was always pointing at that `id`
— starts resolving to an active row again instead of staying orphaned forever. Still worth
revisiting once a `cliente` deactivation feature ships (the race itself is unchanged, only its
consequence is now recoverable), but no longer the one-way data-integrity hazard it used to be.

The identical race exists between `SuministroDirectory` and `ImportLecturas`' write, against
`fk_lecturas_suministro` instead: also currently unreachable (no endpoint deletes/deactivates a
`suministro` either), and now equally healable by re-importing the `suministro`.

## One package, staged entities

`DOMAIN_MODEL.md` §4.3 ("Gestión de Consumos") lists three entities: Lectura, Consumo, and Lote
de Facturación. US-003 implemented `Lectura`; US-005 added `Lote de Facturación` to the same
package (not a new one) — deliberately *before* `Consumo`, even though `Consumo` is listed first
in some places: `consumos.lote_id` is `NOT NULL`, so the FK dependency dictated the
implementation order regardless of documentation order. US-004 then added `Consumo` itself to
this same package, following the same four Clean Architecture layers already established here —
with `Lote`/`Lectura` already in place, both FK dependencies (`lote_id NOT NULL`, `lectura_id`
nullable) were resolvable, and Épica 1 (the three US-001/002/003/005/004 user stories) is now
complete. This mirrors how `categoria_tarifaria` was folded into the `suministros` package
instead of getting its own (`contexts/README.md`, "Internal shape of a context"): the package
boundary is the *bounded context* §4 defines, not a 1:1 mapping to entities or user stories.

## `Lote`: no cross-context (or same-context) foreign key to resolve

Unlike every import use case before it, `ImportLotes` (`application/import_lotes.py`, US-005) has
no directory-port resolution step at all: `lotes` references no other table (it is the other way
around — `consumos`, `feature_vectors`, `predicciones` and `resultados_ia` all reference `lotes`),
so there is no natural key to resolve to a UUID before `Lote.create()` can be attempted. This is
the first entity in the codebase for which that whole pattern (source port + directory port
resolution, see "Source-port pattern for imports" / "Cross-context directory-port pattern" above)
simply does not apply — `ImportLotes` only needs a `LoteSource` and a `LoteRepository`.

## `Lote.estado` is never accepted from the import payload

`Lote` models its `estado` (`domain/lote.py`'s `EstadoLote`) as a real four-value enum
(`Pendiente`/`Procesando`/`Procesado`/`Error`, DOMAIN_MODEL.md §7.4 "Estados"), with an
`ALLOWED_TRANSITIONS` map and a `Lote.transition_to()` method enforcing RD-010 ("un lote no puede
ejecutarse dos veces": no transition back to `Pendiente`, no skipping `Procesando`). The
processing engine that drives these transitions now exists (`motor`, Épica 2 slice 1, see "The
`motor` context" below) — but nothing in `contexts.consumos` itself ever calls `Lote.transition_to()`
in-process: `motor` is a *different* bounded context and, per this document's own cross-context
rule, never imports `Lote` (or anything else from `contexts.consumos`) to do so. `motor` mirrors
the same `EstadoLote`/`ALLOWED_TRANSITIONS` shape in its own domain
(`motor/domain/lote_estado.py`) and writes `lotes.estado` through a direct, optimistic SQL
`UPDATE` instead — see "The `motor` context" below for the full write-boundary rationale. The
invariant has lived in `consumos`' own domain since before `motor` existed, not bolted on later
as an afterthought, and stays the single source of truth both mirrors are checked against.

`Lote.create()` has **no `estado` parameter at all**, not even an optional one defaulting to
`Pendiente`: every freshly created `Lote` is unconditionally born `Pendiente`, by construction.
`LoteImportItem` (`presentation/schemas.py`) mirrors the same omission at the HTTP boundary — if a
caller sends `estado` in the import payload anyway, it is rejected as a per-record structural
violation (`model_config = ConfigDict(extra="forbid")`, HTTP 200, reported in `rejected` and
naming `estado` as the offending key), not a 422 for the whole batch, and not silently dropped
either: `LoteImportItem` used to default to Pydantic's `extra="ignore"`, which had the exact same
effect on `estado` (no way to fabricate a `Procesado` lote through the payload) but also meant a
*typo'd* field name (e.g. `canditad_registros` instead of `cantidad_registros`) vanished the same
invisible way, silently defaulting the real field instead of surfacing any error. The reasoning
for keeping `estado` out of the payload at all: accepting it from an import payload would let a
single crafted request fabricate an already-`Procesado` lote that never actually went through the
pipeline that state represents, exactly the kind of shortcut RD-010 exists to close.
`clientes`/`suministros`/`lecturas`' own import DTOs (`ClienteImportItem`, `SuministroImportItem`,
`LecturaImportItem`) were aligned to the same `extra="forbid"` (decision confirmed by business,
2026-07-13 -- see `PROJECT_MASTER_SPEC.md`, resolved items): an unrecognized key in any of the
three payloads is now rejected the same way, per-record, naming the offending key.

This has a direct consequence for `ImportLotes`' update path, worth calling out because it is the
one place in this codebase where the update-merge logic deliberately does **not** re-validate
through `create()` the way `ImportLecturas`/`ImportSuministros` do: `Lote.create()` cannot express
"keep the existing `estado`", so routing the merge through it would silently reset a `Procesado`
lote back to `Pendiente` on every re-import. `ImportLotes` uses `dataclasses.replace()` on the
*existing* row instead, overriding only `nombre`/`cantidad_registros` — see that module's
docstring for the full rationale. The guarantee is reinforced structurally one layer down too:
`SqlAlchemyLoteRepository.save()`'s `ON CONFLICT DO UPDATE SET` deliberately excludes `estado` (and
`fecha_importacion`) from the columns it writes on an update, so even a stale in-memory `Lote` —
one read before a concurrent transaction changed `estado`, a lost-update race — cannot revert it;
see `infrastructure/lote_repository.py`'s `save()` docstring. Tested end-to-end in
`tests/integration/contexts/consumos/test_lotes_routes_integration.py`
(`test_reimporting_after_the_lote_transitioned_to_procesado_never_resets_its_estado`): import a
lote, flip its `estado` to `Procesado` directly via SQL (simulating what the future processing
engine would do), re-import the same `codigo_lote` with different `nombre`/`cantidad_registros` —
the response reports `updated`, the fields change, and `estado` stays `Procesado`. The race itself
is reproduced directly against the repository in
`tests/integration/contexts/consumos/test_lote_repository_integration.py`
(`test_save_never_reverts_a_concurrently_updated_estado`).

## `Lote` re-import: omitted fields vs. an explicit `null`

`ImportLotes` (`application/import_lotes.py`) treats a `nombre`/`cantidad_registros` field that is
genuinely *absent* from the source record differently from one explicitly sent as `null` —
`LoteSourceRecord` (`domain/ports.py`) represents "absent" as the `UNSET` sentinel, not `None`, so
the distinction survives from the HTTP payload (via `LoteImportItem.model_fields_set`, checked in
`presentation/routes.py`) all the way into the merge decision. On an update, a field left `UNSET`
preserves whatever `existing` already has stored; an explicit value (including an explicit `null`
for `nombre`) overwrites it. Before this existed, an omitted field defaulted through
`Lote.create()` exactly like an explicit `null` would, so re-importing an existing `codigo_lote`
with only some fields repeated (a common partial-update payload shape) silently wiped the fields
left out — `nombre` to `null`, `cantidad_registros` to `0` — instead of leaving them alone.
`cantidad_registros` has one more wrinkle `nombre` does not: it is a `NOT NULL` column with its
own `DEFAULT 0`, so an *explicit* `null` (as opposed to omission) is rejected outright as a
per-record violation, on both create and update, instead of being silently treated the same as an
omission. See `docs/03-architecture/API_SPEC.md` ("Campos omitidos vs. `null` explícito") for the
full table of the three states (`UNSET`/`null`/value) and their effect.

## `Consumo`: `UNSET` reaches into `create()` itself, not just the update-merge step

`Consumo.create()` (`domain/consumo.py`, US-004) is the first `create()` in this codebase whose
own parameter list needs the three-state `UNSET`/`None`/value distinction, for
`consumo_promedio_diario` — every earlier entity's `create()` (`Lote`, `Lectura`, `Suministro`,
`Cliente`) only ever sees a two-state `None`-or-value; `ImportLotes` collapses `LoteSourceRecord`'s
`UNSET` fields to `None` *before* calling `Lote.create()` (see the section above), and does the
`UNSET`-vs-value comparison entirely in its own update-merge step, comparing the raw source record
against the already-built candidate.

That collapsing pattern does not work for `consumo_promedio_diario`, because DOMAIN_MODEL.md
§7.6 lists `calcularPromedioDiario()` as a domain method: when the field is genuinely omitted,
`Consumo.create()` must *compute* the derived average (`kwh / dias_facturados`, quantized to
`numeric(12,3)`) — not just apply some static default the way `Lote.cantidad_registros` defaults
to `0`. An explicit `null`, by contrast, must *skip* computation and store `null` outright. Both
of those are meaningfully different outcomes that only `create()` itself can produce (it is the
one place that already has `kwh` and `dias_facturados` validated and in hand), so `UNSET` had to
become a real parameter default `Consumo.create()` understands, imported from the new
`domain/sentinels.py` module (see that module's docstring for why the sentinel moved out of
`domain/ports.py` — `Consumo` importing it from there would have been a circular import, since
`domain/ports.py` itself imports `Consumo` for `ConsumoRepository`'s signatures).

`ImportConsumos`'s own update-merge step does NOT need a second `UNSET` check for
`consumo_promedio_diario` (FIX 1 — an earlier version of this code mistakenly added one anyway,
comparing the raw `ConsumoSourceRecord.consumo_promedio_diario` against `UNSET` to preserve
`existing`'s already-stored value on omission, the same treatment `ImportLotes` gives its own
opaque fields). That was wrong for a *derived* field: `candidate.consumo_promedio_diario` is
already correct in every one of the three states by the time `Consumo.create()` returns it —
recomputed from *this* record's own `kwh`/`dias_facturados` when omitted, `None` when explicit
`null`, the given value when explicit — so the merge step takes it from `candidate` as-is, never
`existing`'s. Preserving `existing`'s value on omission (as `ImportLotes` correctly does for
`nombre`/`cantidad_registros`, which have no derived inputs of their own to recompute from) left
a stale average on the row whenever `kwh`/`dias_facturados` changed without repeating
`consumo_promedio_diario` too — reproduced case: `kwh` 100 -> 295, `dias_facturados` 31, omitted
`consumo_promedio_diario` froze at 3.226 instead of recomputing to 9.516. See
`application/import_consumos.py`'s module docstring and `domain/consumo.py`'s for the full
contrast. `fecha_lectura`/`lectura_id` follows the same three-state contract for consistency, but
does not need `create()`'s own awareness of `UNSET`, and DOES need the update-merge's
preserve-on-omission treatment (like `Lote`'s opaque fields, unlike `consumo_promedio_diario`):
`ImportConsumos` resolves `fecha_lectura` to a `lectura_id` (or `None`) entirely in the
application layer before ever calling `create()`, the same way `SuministroDirectory`/
`LoteDirectory` resolutions already do, and there is no "recompute from inputs" fallback for a
foreign-key reference the way there is for a numeric average.

## The `motor` context

`motor` (DOMAIN_MODEL.md §4.4, "Motor de Inteligencia Energética") is the fourth bounded context
to land, Épica 2 slice 1 (US-006 + US-010 trigger, Etapa 1 only — validación de integridad;
Etapas 2-8 of `docs/04-ai/AI_ENGINE_SPEC.md` §3 are not started). Package name: `motor`, the
short Spanish domain noun itself (the same naming convention `clientes`/`suministros`/`consumos`
already established), superseding the `intelligence_engine` placeholder this table used before
this context shipped — not `intelligence_engine`, `ai_engine`, or `ia`: the canonical name in
DOMAIN_MODEL.md/AI_ENGINE_SPEC.md is "Motor de Inteligencia Energética", and `motor` is what a
reader of either document would recognize immediately, the same reasoning `clientes`/
`suministros` already used to reject their own English placeholders.

### A cross-context WRITE port, not just a read one

Every directory port before `motor` ("Cross-context directory-port pattern" above) only ever
*resolves* a natural key to a UUID — a read. `motor` needs to both read `lotes.estado`/
`cantidad_registros` and, having decided the outcome, *write* `lotes.estado` (`Pendiente`/
`Error` → `Procesando` → `Procesado`/`Error`) — and `Lote` (the entity, its `ALLOWED_TRANSITIONS`,
`Lote.transition_to()`) belongs entirely to `consumos` (DOMAIN_MODEL.md §4.3), a *different*
bounded context from `motor` (§4.4), even though both share the same physical `lotes` table
(ADR-006). Per this document's own cross-context rule, `motor` never imports
`contexts.consumos.domain.lote.Lote` (or its repository) to perform that write. Instead:

- `motor/domain/lote_estado.py` is a small, deliberate **mirror** of `consumos`' own
  `EstadoLote`/`ALLOWED_TRANSITIONS` (same 4 states, same edges) — the pre-flight check
  `ProcesarLote` runs before ever issuing SQL, the same way `SqlDirectSuministroDirectory`
  duplicates knowledge of `suministros`' shape via raw SQL instead of importing its ORM model.
  The single source of truth for the actual states/transitions is the database's own
  `ck_lotes_estado` CHECK constraint plus DOMAIN_MODEL.md §7.4 — not either mirror.
- `motor/infrastructure/lote_procesamiento.py` (`SqlLoteProcesamientoPort`, implementing
  `LoteProcesamientoPort`, `motor/domain/ports.py`) reads `lotes` via `sqlalchemy.text` and
  WRITES `lotes.estado` via an **optimistic** `UPDATE lotes SET estado = :hacia WHERE id =
  :lote_id AND estado IN (...)`, checking `rowcount` — the concurrency guarantee a concurrent
  trigger race degrades to a `409` response, not a lost update. This is the one write path this
  codebase grants to a context that does not own the table's entity, and it is deliberately
  narrow: one column (`estado`), one table (`lotes`), no other field of `Lote` is ever touched
  from `motor`.
- The consumos-context import repository (`SqlAlchemyLoteRepository`) still deliberately never
  writes `estado` (see "`Lote.estado` is never accepted from the import payload" above) — that
  boundary is unchanged and unweakened by `motor`'s existence. The two write paths are disjoint:
  `ImportLotes` never writes `estado`; `motor` never writes anything else on `lotes`.
- `motor/infrastructure/validacion_data_source.py` (`SqlValidacionDataSource`, implementing
  `ValidacionDataSource`) is a READ-only cross-context port, following the ordinary pattern
  above: one set-based SQL query joining `consumos`/`lecturas`/`suministros`/
  `categorias_tarifarias` (none of which belong to `motor`) to build the chain Etapa 1's checks
  (`motor/domain/checks.py`) evaluate — never a per-row loop of individual queries (RNF-001).

### Single-transaction atomicity, no explicit locking

`ProcesarLote.execute()` (`motor/application/procesar_lote.py`) never calls `session.commit()`;
the route (`motor/presentation/routes.py`) commits exactly once, after the whole sequence
(completeness gate → `Procesando` → checks → `Procesado`/`Error`) succeeds. Postgres's own row
lock on the `Procesando` `UPDATE` blocks any concurrent `procesar` call against the SAME lote
until this transaction commits or rolls back — so a crash between the two transitions rolls the
WHOLE sequence back (the lote is never left stuck at `Procesando`) instead of requiring a
separate recovery mechanism a two-commit design would need.

## No empty ceremony

`clientes`, `suministros`, `consumos` and `motor` exist so far (see `## Internal shape of a
context` above for what it looks like in practice). The other 3 packages are not created yet: a
context package is created only when its first real feature lands — domain entities, a use case,
a repository, whatever comes first for that context. Scaffolding empty layer folders ahead of any
actual code would be ceremony without a behavior behind it, which is exactly what ADR-001's
accepted trade-offs warn against for a single-developer team.
