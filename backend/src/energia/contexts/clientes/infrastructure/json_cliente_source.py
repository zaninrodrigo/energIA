"""JsonClienteSource: today's `ClienteSource` adapter — the HTTP request payload.

Per US-001 / ADR-004, there is no Oracle access yet, so this is the only `ClienteSource`
adapter that exists today. A file adapter (CSV/Excel upload) and, eventually, the real Oracle
ETL adapter can implement the same `ClienteSource` port (domain/ports.py) later without any
change to domain or application code.
"""

from collections.abc import Iterable, Iterator

from energia.contexts.clientes.domain.ports import ClienteSourceRecord


class JsonClienteSource:
    """Wraps a list of `ClienteSourceRecord`s already parsed from the request's JSON array
    (see `presentation/routes.py`, which builds them from the validated Pydantic body) so
    `ImportClientes` can consume it through the `ClienteSource` port.
    """

    def __init__(self, records: Iterable[ClienteSourceRecord]) -> None:
        self._records = list(records)

    def fetch(self) -> Iterator[ClienteSourceRecord]:
        return iter(self._records)
