"""JsonLecturaSource: today's `LecturaSource` adapter -- the HTTP request payload.

Mirrors `contexts.suministros.infrastructure.json_suministro_source.JsonSuministroSource`
exactly. Per US-003 / ADR-004, there is no Oracle access yet, so this is the only `LecturaSource`
adapter that exists today. A file adapter (CSV/Excel upload) and, eventually, the real Oracle ETL
adapter can implement the same `LecturaSource` port (domain/ports.py) later without any change to
domain or application code.
"""

from collections.abc import Iterable, Iterator

from energia.contexts.consumos.domain.ports import LecturaSourceRecord


class JsonLecturaSource:
    """Wraps a list of `LecturaSourceRecord`s already parsed from the request's JSON array (see
    `presentation/routes.py`, which builds them from the validated Pydantic body) so
    `ImportLecturas` can consume it through the `LecturaSource` port.
    """

    def __init__(self, records: Iterable[LecturaSourceRecord]) -> None:
        self._records = list(records)

    def fetch(self) -> Iterator[LecturaSourceRecord]:
        return iter(self._records)
