# Bounded contexts

Per ADR-001 (Clean Architecture + tactical DDD) and ADR-006 (modular monolith), each bounded
context from `docs/03-architecture/DOMAIN_MODEL.md` §4 becomes a Python package under
`contexts/`, not a separate service or deployment. Module boundaries — not network
boundaries — are what the runtime enforces today; extracting a context into its own service
later remains possible without a redesign (ADR-006).

## The 7 bounded contexts

| Package (English, code identifier) | Bounded context (`DOMAIN_MODEL.md` §4, canonical name) |
|---|---|
| `customers` | Gestión de Clientes |
| `supplies` | Gestión de Suministros |
| `consumption` | Gestión de Consumos |
| `intelligence_engine` | Motor de Inteligencia Energética |
| `risk` | Gestión del Riesgo |
| `inspections` | Gestión de Inspecciones |
| `dashboard` | Dashboard Ejecutivo |

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

## No empty ceremony

None of the packages above exist yet. A context package is created only when its first real
feature lands — domain entities, a use case, a repository, whatever comes first for that
context. Scaffolding four empty layer folders ahead of any actual code would be ceremony
without a behavior behind it, which is exactly what ADR-001's accepted trade-offs warn
against for a single-developer team.
