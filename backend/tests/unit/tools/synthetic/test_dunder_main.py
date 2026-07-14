"""Unit test for the `python -m energia.tools.synthetic` entrypoint module.

Mirrors `tests/unit/test_main.py`'s pattern: importing the module exercises its top-level
statements without ever running the `if __name__ == "__main__":` guarded call (which only fires
when the module is executed directly, not when imported).
"""

from energia.tools.synthetic import __main__ as dunder_main


def test_dunder_main_exposes_the_cli_main_function() -> None:
    assert dunder_main.main.__module__ == "energia.tools.synthetic.cli"
