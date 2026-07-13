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
| `consumos` | Gestión de Consumos | **Implemented** (US-003, `Lectura` only) |
| `intelligence_engine` | Motor de Inteligencia Energética | Not started |
| `risk` | Gestión del Riesgo | Not started |
| `inspections` | Gestión de Inspecciones | Not started |
| `dashboard` | Dashboard Ejecutivo | Not started |

`clientes` is the first context implemented and sets the two conventions below. `suministros`
(§4.2) is the second — note it is the Spanish domain noun itself (per the naming convention),
superseding the `supplies` placeholder this table used before that context shipped. `consumos`
(§4.3) is the third, superseding the `consumption` placeholder the same way — see "One package,
staged entities" below for why it ships today with only `Lectura` implemented, ahead of
`Consumo`/`Lote de Facturación`. Package names for contexts not yet started are still
placeholders (English, one option among several); the `clientes`/`suministros`/`consumos` naming
convention takes precedence once each context actually lands — see below.

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

Re-importing a `numero_cliente` whose row is soft-deleted (`deleted_at IS NOT NULL`) creates a
**brand-new row with a new `id`** — it does not "resurrect" the original row. This follows
directly from `uq_clientes_numero_cliente` being a *partial* unique index (`WHERE deleted_at IS
NULL`, `docker/postgres/init/01_schema.sql`): a soft-deleted `numero_cliente` is free to be
reused because the index no longer constrains it, and `ImportClientes`/`SqlAlchemyClienteRepository.save()`
never look past `deleted_at IS NULL` rows when deciding create-vs-update (`get_by_numero_cliente`
filters them out) or when upserting (the natural-key `ON CONFLICT` target is scoped by the same
`WHERE deleted_at IS NULL` predicate).

This is today's deliberate, tested behavior (see `test_reimporting_a_soft_deleted_numero_
cliente_creates_a_new_identity`, `tests/integration/contexts/clientes/
test_clientes_routes_integration.py`), not a bug — but it has a real consequence: any
historical FK pointing at the old row (e.g. `suministros.cliente_id`) keeps pointing at the old,
still-soft-deleted row, never at the new one. Whether the business actually wants resurrection
instead is an open question — see `PROJECT_MASTER_SPEC.md`'s debt list.

`suministros` follows the exact same semantics for `numero_suministro`
(`uq_suministros_numero_suministro` is the same kind of partial unique index), tested by
`test_reimporting_a_soft_deleted_numero_suministro_creates_a_new_identity`
(`tests/integration/contexts/suministros/test_suministros_routes_integration.py`) — no new
resurrection logic was introduced for the second context either.

`consumos` follows the same semantics for `Lectura`, keyed by the *composite* natural key
`(suministro_id, fecha_lectura)` instead of a single column: `uq_lecturas_suministro_fecha`
(`docker/postgres/init/01_schema.sql`) is a partial unique index over that pair (`WHERE
deleted_at IS NULL`), added as part of US-003 — `lecturas` had no natural-key unique index at
all before this slice, so re-importing the same historical reading would otherwise have
duplicated it on every re-run instead of upserting. Tested by
`test_reimporting_a_soft_deleted_key_creates_a_new_identity`
(`tests/integration/contexts/consumos/test_lecturas_routes_integration.py`).

**Known FK race with a soft-deleted `cliente`**: `fk_suministros_cliente`
(`docker/postgres/init/01_schema.sql`) is an ordinary foreign key against `clientes.id` — it does
not, and cannot, check `deleted_at`. If a `cliente` were soft-deleted *between* `ClienteDirectory`
resolving its `id` and `ImportSuministros`' `INSERT`/`UPDATE` of the `suministro` row, the new or
updated `suministro` would end up referencing a now soft-deleted `cliente`, the same way a
historical FK can already point at one (see above). This is currently unreachable in practice —
the API exposes no endpoint to delete/deactivate a `cliente` at all — but it must be revisited
once a `cliente` deactivation feature ships, since that endpoint would make the race a real,
if narrow, concurrency window.

The identical race exists between `SuministroDirectory` and `ImportLecturas`' write, against
`fk_lecturas_suministro` instead: also currently unreachable (no endpoint deletes/deactivates a
`suministro` either), for the same reason.

## One package, staged entities

`DOMAIN_MODEL.md` §4.3 ("Gestión de Consumos") lists three entities: Lectura, Consumo, and Lote
de Facturación. US-003 only implements `Lectura` — `Consumo` and `Lote de Facturación` have no
domain entity, table-facing repository, or endpoint yet, and will be added to this same
`consumos` package (not a new one) as their own user stories land, each following the same four
Clean Architecture layers already established here. This mirrors how `categoria_tarifaria` was
folded into the `suministros` package instead of getting its own (`contexts/README.md`,
"Internal shape of a context"): the package boundary is the *bounded context* §4 defines, not a
1:1 mapping to entities or user stories.

## No empty ceremony

Only `clientes`, `suministros` and `consumos` exist so far (see `## Internal shape of a context`
above for what it looks like in practice). The other 4 packages are not created yet: a context
package is created only when its first real feature lands — domain entities, a use case, a
repository, whatever comes first for that context. Scaffolding four empty layer folders ahead of
any actual code would be ceremony without a behavior behind it, which is exactly what ADR-001's
accepted trade-offs warn against for a single-developer team.
