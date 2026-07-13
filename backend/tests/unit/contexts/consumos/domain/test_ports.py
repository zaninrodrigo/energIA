"""Unit tests for `domain/ports.py`'s `UNSET` sentinel (FIX 1).

`LoteSourceRecord`'s own dataclass-level default (`UNSET`, not `None`) is exercised end-to-end by
`tests/unit/contexts/consumos/application/test_import_lotes.py` and the HTTP-level tests in
`tests/integration/contexts/consumos/test_lotes_routes_integration.py`; this module covers
`UnsetType`'s own small surface directly instead.
"""

import copy

from energia.contexts.consumos.domain.ports import UNSET, UnsetType


def test_unset_reprs_as_unset() -> None:
    assert repr(UNSET) == "UNSET"


def test_unset_is_an_instance_of_unset_type() -> None:
    assert isinstance(UNSET, UnsetType)


def test_deepcopying_unset_preserves_its_singleton_identity() -> None:
    """`dataclasses.asdict()` (used by `presentation/routes.py` to echo a rejected record back in
    the HTTP response) deep-copies every field value -- without `UnsetType.__deepcopy__`
    returning `self`, every `is UNSET`/`isinstance(..., UnsetType)` check downstream of that copy
    would still work (a second instance is still a `UnsetType`), but the specific `is UNSET`
    identity check used in a few call sites would silently stop matching.
    """
    copied = copy.deepcopy(UNSET)

    assert copied is UNSET
