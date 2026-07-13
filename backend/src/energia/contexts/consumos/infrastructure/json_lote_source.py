"""JsonLoteSource: today's `LoteSource` adapter -- the HTTP request payload.

Mirrors `contexts.consumos.infrastructure.json_lectura_source.JsonLecturaSource` exactly. Per
US-005 / ADR-004, there is no Oracle access yet, so this is the only `LoteSource` adapter that
exists today. A file adapter (CSV/Excel upload) and, eventually, the real Oracle ETL adapter can
implement the same `LoteSource` port (domain/ports.py) later without any change to domain or
application code.
"""

from collections.abc import Iterable, Iterator

from energia.contexts.consumos.domain.ports import LoteSourceRecord


class JsonLoteSource:
    """Wraps a list of `LoteSourceRecord`s already parsed from the request's JSON array (see
    `presentation/routes.py`, which builds them from the validated Pydantic body) so
    `ImportLotes` can consume it through the `LoteSource` port.
    """

    def __init__(self, records: Iterable[LoteSourceRecord]) -> None:
        self._records = list(records)

    def fetch(self) -> Iterator[LoteSourceRecord]:
        return iter(self._records)
