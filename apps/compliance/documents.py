"""The model withdrawal form as a PDF (I18N.20), built from the page's translated strings."""

from django.template.loader import render_to_string
from django.utils import translation
from django.utils.translation import gettext

from apps.core.pdf import render_html

TEMPLATE = "documents/compliance/model_withdrawal_form.html"


def render_model_withdrawal_form_pdf(language: str) -> bytes:
    """The blank form in `language`. It includes the very template the consumer information page
    shows, so the two cannot drift, and takes no order data. Raises `AttachmentUnavailable` when
    the renderer fails."""
    with translation.override(language):
        html = render_to_string(TEMPLATE, {"title": gettext("Model withdrawal form")})
    return render_html(html)
