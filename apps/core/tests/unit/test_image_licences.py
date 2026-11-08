"""CMS.8: licence allow-list rules, required image metadata and credit lines (spec 10 §10.2).

Pure rules, no database: `apps.core.image_licences`.
"""

import pytest

from apps.core.image_licences import (
    Credit,
    LicenceTerms,
    credit_for,
    metadata_errors,
    restricted_licence_error,
)

CC_BY = LicenceTerms(
    code="CC-BY-4.0",
    name="CC BY 4.0",
    url="https://creativecommons.org/licenses/by/4.0/",
    attribution_required=True,
    source_required=True,
    permission_required=False,
)
CC0 = LicenceTerms(
    code="CC0-1.0",
    name="CC0 1.0",
    url="https://creativecommons.org/publicdomain/zero/1.0/",
    attribution_required=False,
    source_required=True,
    permission_required=False,
)
OWN_WORK = LicenceTerms(
    code="own-work",
    name="Own work",
    url="",
    attribution_required=False,
    source_required=False,
    permission_required=False,
)
WRITTEN_PERMISSION = LicenceTerms(
    code="written-permission",
    name="Written permission",
    url="",
    attribution_required=True,
    source_required=True,
    permission_required=True,
)


@pytest.mark.parametrize(
    ("code", "name", "url"),
    [
        ("CC-BY-NC-4.0", "CC BY-NC 4.0", ""),
        ("CC-BY-ND-4.0", "CC BY-ND 4.0", ""),
        ("CC-BY-NC-SA-4.0", "CC BY-NC-SA 4.0", ""),
        ("cc-by-nc-nd-4.0", "Some licence", ""),
        ("custom", "Attribution-NonCommercial 4.0 International", ""),
        ("custom", "Attribution-NoDerivatives 4.0 International", ""),
        ("custom", "Custom", "https://creativecommons.org/licenses/by-nc/4.0/"),
        ("custom", "Custom", "https://creativecommons.org/licenses/by-nd/2.0/"),
    ],
)
def test_non_commercial_and_no_derivatives_licences_are_refused(
    code: str, name: str, url: str
) -> None:
    assert restricted_licence_error(code, name, url) is not None


@pytest.mark.parametrize(
    ("code", "name"),
    [
        ("own-work", "Own work"),
        ("vendor-supplied", "Vendor supplied"),  # "vendor" contains "nd": not a licence term
        ("written-permission", "Written permission"),
        ("CC0-1.0", "CC0 1.0 Universal"),
        ("CC-BY-4.0", "CC BY 4.0"),
        ("CC-BY-SA-4.0", "CC BY-SA 4.0"),
    ],
)
def test_the_seeded_licences_are_allowed(code: str, name: str) -> None:
    assert restricted_licence_error(code, name, "") is None


def test_an_image_needs_a_licence_and_an_author() -> None:
    errors = metadata_errors(None, author=" ", source_url="", permission_ref="")
    assert set(errors) == {"licence", "author"}


def test_a_free_licence_needs_the_source_url() -> None:
    errors = metadata_errors(CC_BY, author="Ada", source_url="", permission_ref="")
    assert set(errors) == {"source_url"}


def test_own_work_needs_no_source_url() -> None:
    assert metadata_errors(OWN_WORK, author="Operator", source_url="", permission_ref="") == {}


def test_written_permission_needs_the_permission_reference() -> None:
    errors = metadata_errors(
        WRITTEN_PERMISSION, author="Ada", source_url="https://example.com/a", permission_ref=""
    )
    assert set(errors) == {"permission_ref"}


def test_a_cc_by_image_is_credited_with_author_licence_and_source() -> None:
    credit = credit_for(
        CC_BY, author="Ada Lovelace", source_url="https://example.com/photo", credit_text=""
    )
    assert credit == Credit(
        text="Ada Lovelace",
        licence_name="CC BY 4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        source_url="https://example.com/photo",
    )


def test_the_credit_text_overrides_the_generated_line() -> None:
    credit = credit_for(
        CC_BY, author="Ada", source_url="https://example.com/a", credit_text="Photo by Ada L."
    )
    assert credit is not None
    assert credit.text == "Photo by Ada L."


def test_a_cc0_image_has_no_credit() -> None:
    assert credit_for(CC0, author="Ada", source_url="https://example.com/a", credit_text="") is None


def test_an_image_without_licence_has_no_credit() -> None:
    assert credit_for(None, author="Ada", source_url="", credit_text="") is None
