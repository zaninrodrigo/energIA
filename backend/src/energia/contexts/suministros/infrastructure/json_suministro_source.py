"""JsonSuministroSource: today's `SuministroSource` adapter -- the HTTP request payload.

Mirrors `contexts.clientes.infrastructure.json_cliente_source.JsonClienteSource` exactly. Per
US-002 / ADR-004, there is no Oracle access yet, so this is the only `SuministroSource` adapter
that exists today. A file adapter (CSV/Excel upload) and, eventually, the real Oracle ETL
adapter can implement the same `SuministroSource` port (domain/ports.py) later without any
change to domain or application code.
"""

from collections.abc import Iterable, Iterator

from energia.contexts.suministros.domain.ports import SuministroSourceRecord


class JsonSuministroSource:
    """Wraps a list of `SuministroSourceRecord`s already parsed from the request's JSON array
    (see `presentation/routes.py`, which builds them from the validated Pydantic body) so
    `ImportSuministros` can consume it through the `SuministroSource` port.
    """

    def __init__(self, records: Iterable[SuministroSourceRecord]) -> None:
        self._records = list(records)

    def fetch(self) -> Iterator[SuministroSourceRecord]:
        return iter(self._records)
