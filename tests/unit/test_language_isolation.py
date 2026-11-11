"""A language activated by one test (a request through LocaleMiddleware does it) must not reach the
next one: the root conftest resets it, so `i18n_patterns` URLs resolve the same in every order."""

from django.conf import settings
from django.utils import translation


def test_a_test_may_leave_a_language_active() -> None:
    translation.activate("it")
    assert translation.get_language() == "it"


def test_the_next_test_starts_in_the_default_language() -> None:
    assert translation.get_language() == settings.LANGUAGE_CODE
