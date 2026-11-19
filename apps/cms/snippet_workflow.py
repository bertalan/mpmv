"""One rule for every snippet publish path (CMS.14): no active review workflow, no publish.

The Wagtail admin (`before_publish` hook), the API, a shell (`revision.publish()`) and the
scheduled job (`publish_scheduled`) all ask `snippet_can_publish`, and refuse with the same
message. The model enforces it in `ContentSnippet.publish`, the one method every path reaches.
"""

from typing import Any

from django.core.exceptions import PermissionDenied
from django.utils.translation import gettext_lazy as _

REFUSED_MESSAGE = _(
    'This snippet cannot be published because the "Content snippet review" '
    "workflow is missing or switched off. Your changes are saved as a draft. "
    "Ask an administrator to restore the workflow."
)


class SnippetPublishRefused(PermissionDenied):
    """Raised outside the admin when a snippet would be published without its review workflow."""


def snippet_can_publish(snippet: Any) -> bool:
    """True when the "Content snippet review" workflow exists and is active."""
    return bool(snippet.get_workflow() is not None)
