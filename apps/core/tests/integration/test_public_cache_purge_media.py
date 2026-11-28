"""SEO.6 2b: an image or document edit purges the public pages."""

from collections.abc import Callable
from typing import Any

import pytest
from django.core.files.base import ContentFile
from wagtail.documents import get_document_model

from apps.core import public_cache
from apps.core.tests.factories import MediaImageFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def purges(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(public_cache, "_listeners", [seen.append])
    return seen


Commit = Callable[..., Any]


def test_an_image_edit_and_delete_purge(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        image = MediaImageFactory.create()
    assert "image" in purges
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        image.title = "Renamed"
        image.save()
    assert purges == ["image"]
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        image.delete()
    assert purges == ["image"]


def test_a_document_edit_and_delete_purge(
    purges: list[str], django_capture_on_commit_callbacks: Commit
) -> None:
    document = get_document_model()(title="Sheet")
    document.file.save("sheet.pdf", ContentFile(b"%PDF-1.4"), save=False)
    with django_capture_on_commit_callbacks(execute=True):
        document.save()
    assert "document" in purges
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        document.title = "Renamed"
        document.save()
    assert purges == ["document"]
    purges.clear()
    with django_capture_on_commit_callbacks(execute=True):
        document.delete()
    assert purges == ["document"]
