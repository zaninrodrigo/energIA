"""JsonConsumoSource: today's `ConsumoSource` adapter -- the HTTP request payload.

Mirrors `contexts.consumos.infrastructure.json_lectura_source.JsonLecturaSource` exactly. Per
US-004 / ADR-004, there is no Oracle access yet, so this is the only `ConsumoSource` adapter that
exists today. A file adapter (CSV/Excel upload) and, eventually, the real Oracle ETL adapter can
implement the same `ConsumoSource` port (domain/ports.py) later without any change to domain or
application code.
"""

from collections.abc import Iterable, Iterator

from energia.contexts.consumos.domain.ports import ConsumoSourceRecord


class JsonConsumoSource:
    """Wraps a list of `ConsumoSourceRecord`s already parsed from the request's JSON array (see
    `presentation/routes.py`, which builds them from the validated Pydantic body) so
    `ImportConsumos` can consume it through the `ConsumoSource` port.
    """

    def __init__(self, records: Iterable[ConsumoSourceRecord]) -> None:
        self._records = list(records)

    def fetch(self) -> Iterator[ConsumoSourceRecord]:
        return iter(self._records)
