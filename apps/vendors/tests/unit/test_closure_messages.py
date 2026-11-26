"""VEN.20: every error code of `closure_errors` has a translated message."""

from apps.vendors import engine
from apps.vendors.services import closure_messages


def test_every_closure_error_code_has_a_message() -> None:
    codes = {
        value
        for name, value in vars(engine).items()
        if name.startswith("CLOSURE_") and isinstance(value, str)
    }
    assert codes
    assert codes <= set(closure_messages())
