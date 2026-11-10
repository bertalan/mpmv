"""CORE.8 part 3/3: the event moderators role works on the events part of the page tree only."""

import pytest
from wagtail.models import Page

from apps.accounts.models import User
from apps.cms import roles
from apps.cms.tests.factories import (
    BlogPostPageFactory,
    HomePageFactory,
    LegalPageFactory,
    editor_user,
)
from apps.events.tests.factories import EventIndexPageFactory, EventPageFactory, primary_index

pytestmark = pytest.mark.django_db


def _can_edit(user: User, page: Page) -> bool:
    return bool(page.permissions_for_user(User.objects.get(pk=user.pk)).can_edit())


def test_event_moderators_edit_events_and_nothing_else() -> None:
    user = editor_user(roles.EVENT_MODERATORS)
    assert _can_edit(user, primary_index())
    assert _can_edit(user, EventPageFactory.create())
    assert not _can_edit(user, BlogPostPageFactory.create())
    assert not _can_edit(user, LegalPageFactory.create())


@pytest.mark.parametrize("role", [roles.BLOG_EDITORS, roles.LEGAL_EDITORS, roles.PAGE_EDITORS])
def test_other_roles_do_not_edit_events(role: str) -> None:
    user = editor_user(role)
    assert not _can_edit(user, primary_index())
    assert not _can_edit(user, EventPageFactory.create())


def test_an_events_index_added_later_is_covered() -> None:
    index = EventIndexPageFactory.create(parent=HomePageFactory.create(), slug="events-later")
    assert _can_edit(editor_user(roles.EVENT_MODERATORS), index)
