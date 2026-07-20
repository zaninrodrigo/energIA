"""Unit tests for `resultados_ranking_repository`'s pure helpers -- no DB, no session.

Covers `_parse_observaciones`'s defensive parsing of `resultados_ia.observaciones` (DEC-016's
explicability breakdown, stored as JSON text): a malformed/unparseable value must yield an empty
tuple rather than raising -- this is a READ path over already-persisted data, so a corrupt row
must not 500 the whole ranking (see `domain/ports.py`'s `ResultadoRankingRow` docstring).
"""

from energia.contexts.motor.domain.ire import FactorContribucion
from energia.contexts.motor.infrastructure.resultados_ranking_repository import (
    _parse_observaciones,
)


def test_parse_observaciones_returns_empty_tuple_for_none() -> None:
    assert _parse_observaciones(None) == ()


def test_parse_observaciones_returns_empty_tuple_for_empty_string() -> None:
    assert _parse_observaciones("") == ()


def test_parse_observaciones_returns_empty_tuple_for_malformed_json() -> None:
    """`'{not valid json'` must not raise `json.JSONDecodeError` -- defensively returns `()`."""
    assert _parse_observaciones("{not valid json") == ()


def test_parse_observaciones_returns_empty_tuple_when_json_is_not_a_list() -> None:
    """A JSON object (or scalar) is structurally wrong for DEC-016's "lista de objetos" shape."""
    assert _parse_observaciones('{"factor": "score_ia"}') == ()


def test_parse_observaciones_skips_entries_missing_a_required_key() -> None:
    """One malformed entry inside an otherwise-valid list is skipped individually, not fatal to
    the whole list (mirrors the import endpoints' per-record rejection discipline elsewhere in
    this codebase)."""
    raw = '[{"factor": "score_ia", "contribution": 26.09, "reason": "x"}, {"factor": "incompleto"}]'

    result = _parse_observaciones(raw)

    assert result == (FactorContribucion(factor="score_ia", contribution=26.09, reason="x"),)


def test_parse_observaciones_parses_a_well_formed_list() -> None:
    raw = (
        '[{"factor": "score_ia", "contribution": 26.09, "reason": "x"}, '
        '{"factor": "persistencia_anomalias", "contribution": 10.5, "reason": "y"}]'
    )

    result = _parse_observaciones(raw)

    assert result == (
        FactorContribucion(factor="score_ia", contribution=26.09, reason="x"),
        FactorContribucion(factor="persistencia_anomalias", contribution=10.5, reason="y"),
    )
