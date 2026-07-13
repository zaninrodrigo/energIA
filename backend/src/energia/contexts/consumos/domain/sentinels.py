"""`UNSET` sentinel: shared by `domain/ports.py` (source records) and `domain/consumo.py` (the
entity itself), which is why it lives in its own module instead of either of theirs.

Extracted out of `domain/ports.py` (its original home, alongside `LoteSourceRecord`, US-005) when
`Consumo.create()` (US-004) became the first `create()` method that itself needs to distinguish
omitted-from-null for one of its parameters (`consumo_promedio_diario` -- see that module's
docstring for why). `domain/ports.py` imports `Consumo` (for `ConsumoRepository`'s signatures),
so `domain/consumo.py` importing `UNSET` back from `domain/ports.py` would be a circular import;
importing it from this leaf module instead breaks that cycle. `domain/ports.py` still re-exports
both names (`from energia...domain.sentinels import UNSET, UnsetType`) so every existing call site
(`application/import_lotes.py`, `presentation/routes.py`) keeps importing them from `domain.ports`
unchanged.
"""


class UnsetType:
    """Sentinel type for a source-record field genuinely *absent* from the payload -- distinct
    from an explicit `None`. A dedicated, public type (not a bare `object()`, and not
    name-mangled private) so it is nameable both in type hints and in `isinstance()` checks:
    callers narrow with `isinstance(value, UnsetType)` rather than `value is UNSET` -- mypy
    narrows a `Union` type on `isinstance()` but not on an identity (`is`) check against an
    arbitrary object instance, only against `None`/literals/enum members. Still only ever one
    instance in practice (the singleton `UNSET` below); `isinstance()` is used here purely for
    mypy's benefit, not because more than one instance could ever meaningfully exist.
    """

    def __repr__(self) -> str:
        return "UNSET"

    def __deepcopy__(self, memo: dict[int, object]) -> "UnsetType":
        # `dataclasses.asdict()` (used by `presentation/routes.py` to echo a rejected record back
        # in the HTTP response body) deep-copies every field value by default; without this, the
        # singleton identity `value is UNSET` checks (still used where only a boolean "was this
        # provided" comparison is needed, not narrowing) would not survive that copy.
        return self


UNSET = UnsetType()
