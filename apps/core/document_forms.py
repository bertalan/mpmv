"""The Wagtail document form base (`WAGTAILDOCS_DOCUMENT_FORM_BASE`), SEC.2.

Wagtail documents are installed only for wagtail-localize (I18N.3) and their menu is hidden, but
their admin views still take uploads (single, multiple and chooser). Every one of them builds its
form from this base: the file must be a PDF by content (`apps.core.uploads.check_document`),
and is stored under a name the app makes.
"""

from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from wagtail.documents.forms import BaseDocumentForm

from .fields import read_upload
from .uploads import UploadRefused, check_document


class DocumentForm(BaseDocumentForm):  # type: ignore[misc]  # Wagtail is untyped
    def clean_file(self) -> Any:
        upload = self.cleaned_data.get("file")
        if upload is None or getattr(upload, "_committed", False) or upload == self.original_file:
            return upload  # no new file
        limit = settings.WAGTAILDOCS_MAX_UPLOAD_SIZE
        try:
            upload.name = check_document(read_upload(upload, limit), upload.name, max_bytes=limit)
        except UploadRefused as refusal:
            raise ValidationError(refusal.message, code=refusal.code) from None
        return upload
