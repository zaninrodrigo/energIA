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
| `supplies` | Gestión de Suministros | Not started |
| `consumption` | Gestión de Consumos | Not started |
| `intelligence_engine` | Motor de Inteligencia Energética | Not started |
| `risk` | Gestión del Riesgo | Not started |
| `inspections` | Gestión de Inspecciones | Not started |
| `dashboard` | Dashboard Ejecutivo | Not started |

`clientes` is the first context implemented and sets the two conventions below. Package names
for contexts not yet started are placeholders (English, one option among several); the
`clientes` naming convention takes precedence once a context actually lands — see below.

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
historical FK pointing at the old row (e.g. a future `suministros.cliente_id`) keeps pointing
at the old, still-soft-deleted row, never at the new one. Whether the business actually wants
resurrection instead is an open question — see `PROJECT_MASTER_SPEC.md`'s debt list.

## No empty ceremony

Only `clientes` exists so far (see `## Internal shape of a context` above for what it looks
like in practice). The other 6 packages are not created yet: a context package is created only
when its first real feature lands — domain entities, a use case, a repository, whatever comes
first for that context. Scaffolding four empty layer folders ahead of any actual code would be
ceremony without a behavior behind it, which is exactly what ADR-001's accepted trade-offs warn
against for a single-developer team.
