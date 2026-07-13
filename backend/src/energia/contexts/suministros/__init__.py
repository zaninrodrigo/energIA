"""Bounded context: Gestión de Suministros (DOMAIN_MODEL.md §4.2).

Second bounded context implemented (US-002). Follows the same conventions `clientes`
established (see `contexts/README.md`): Spanish domain nouns, English technical parts, and the
source-port pattern for the import use case. Also introduces the cross-context directory-port
pattern (see `contexts/README.md`) for resolving `Cliente` by its natural key without importing
`contexts.clientes` directly.
"""
