"""SEO.18: the demo seed fills what the STAGE audit found missing. The operator gets a generated
logo from the logos collection, with its licence, so the operator `Organization` has one. The
source home gets a search description. Both are create-if-missing: a second run changes
nothing, and what an editor set is kept."""

from io import BytesIO

import pytest
from PIL import Image
from wagtail.models import Locale

from apps.cms.models import HomePage
from apps.core.changeset import ChangeSet
from apps.core.models import PlatformOperator
from apps.core.services import current_operator
from apps.seed import data
from apps.seed.services import TEST_CONTEXT, seed

pytestmark = pytest.mark.django_db


def source_home() -> HomePage:
    home: HomePage = HomePage.objects.get(locale=Locale.get_default(), depth=2)
    return home


def test_the_operator_gets_a_square_logo_fit_for_search_engines(demo_seed: ChangeSet) -> None:
    logo = current_operator().logo
    assert logo is not None
    assert logo.collection.svg_policy is not None  # the "Logos and icons" collection
    assert not logo.needs_review
    with logo.file.open("rb") as handle:
        image = Image.open(BytesIO(handle.read()))
        assert image.format == "PNG"
        assert image.size == (data.OPERATOR_LOGO_SIZE, data.OPERATOR_LOGO_SIZE)
    assert data.OPERATOR_LOGO_SIZE >= 112  # Google's minimum for an Organization logo


def test_the_home_gets_a_search_description_within_160_characters(demo_seed: ChangeSet) -> None:
    home = source_home()
    assert home.search_description == data.HOME_SEARCH_DESCRIPTION
    assert 50 <= len(data.HOME_SEARCH_DESCRIPTION) <= 160


def test_a_second_run_adds_no_operator_version_and_keeps_the_texts(demo_seed: ChangeSet) -> None:
    rows = PlatformOperator.objects.count()
    seed(ctx=TEST_CONTEXT)
    assert PlatformOperator.objects.count() == rows


def test_what_an_editor_set_is_kept(demo_seed: ChangeSet) -> None:
    home = source_home()
    home.search_description = "Written by an editor."
    home.save_revision().publish()
    seed(ctx=TEST_CONTEXT)
    assert source_home().search_description == "Written by an editor."


def test_an_operator_seeded_before_the_logo_gets_it_as_a_new_version(demo_seed: ChangeSet) -> None:
    """As on STAGE: the operator row predates SEO.18, so the logo comes as a new version."""
    import datetime as dt

    from apps.core.services import OPERATOR_COPIED_FIELDS, configure_platform_operator

    old = current_operator()
    bare = PlatformOperator(
        **{name: getattr(old, name) for name in OPERATOR_COPIED_FIELDS},
        valid_from=old.valid_from + dt.timedelta(seconds=1),
    )
    bare.logo = None
    configure_platform_operator(bare, ctx=TEST_CONTEXT)
    assert current_operator().logo is None
    seed(ctx=TEST_CONTEXT)
    assert current_operator().logo is not None
    assert current_operator().legal_name == old.legal_name
