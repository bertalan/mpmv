"""The Wagtail image form base (`WAGTAILIMAGES_IMAGE_FORM_BASE`), THM.12 and SEC.2.

SEC.2: Wagtail's upload field reads the image size through Willow, which decodes some formats
fully, and it only expects a decompression-bomb error there: a truncated or corrupt file raises
`OSError` (or `SyntaxError`, `ValueError`...) out of the form, a server error instead of a
refusal. `SafeImageField` turns any such failure into the field's "not a valid image" error.
The content checks themselves are `MediaImage.clean` (apps.core.uploads), on every path.

THM.12:
Wagtail's multiple upload works in two steps when the image model has required metadata (the
licence and author, CMS.8): step 1 stores the file as an `UploadedFile`, step 2 posts a form
without the file, whose collection the editor may change. The model never sees the file while
that form is validated, so this form checks an SVG upload against the chosen collection and the
sanitiser itself, and shows the refusal in the form. `MediaImage.save` enforces the same rule
whatever the path; this form only turns it into a form error instead of a server error.
"""

import re
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from wagtail.images.fields import WagtailImageField
from wagtail.images.forms import BaseImageForm
from wagtail.models import UploadedFile

from .models import MediaImage, check_svg, read_svg

# The prefix Wagtail gives the step-2 form: `edit_upload_form_prefix` and the upload's id.
_UPLOAD_PREFIX = re.compile(r"uploaded-image-(\d+)")


class SafeImageField(WagtailImageField):  # type: ignore[misc]  # Wagtail is untyped
    def to_python(self, data: Any) -> Any:
        try:
            return super().to_python(data)
        except ValidationError:
            raise
        except Exception as exc:  # whatever an unreadable file makes Pillow raise
            raise ValidationError(
                self.error_messages["invalid_image"], code="invalid_image"
            ) from exc


class MediaImageForm(BaseImageForm):  # type: ignore[misc]  # Wagtail is untyped
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._user = kwargs.get("user")
        super().__init__(*args, **kwargs)
        field = self.fields.get("file")
        if field is not None and type(field) is WagtailImageField:
            # Same field, safer parsing: keeps its widget, label, limits and messages (SEC.2).
            field.__class__ = SafeImageField

    def _post_clean(self) -> None:
        super()._post_clean()
        if self.errors and self.original_file and self.instance.file is not self.original_file:
            # A refused replacement (SEC.2): the edit page shows the form again with a preview
            # rendition of `instance.file`, which Django has already set to the refused upload.
            # The stored image is put back, so no rendition is ever made from a refused file.
            self.instance.file = self.original_file

    def _pending_upload(self) -> UploadedFile | None:
        """The `UploadedFile` this step-2 form turns into an image, if it is one."""
        match = _UPLOAD_PREFIX.fullmatch(self.prefix or "")
        if match is None or self.instance.pk or self.instance.file:
            return None
        upload: UploadedFile | None = UploadedFile.objects.filter(
            pk=int(match.group(1)),
            for_content_type=ContentType.objects.get_for_model(MediaImage),
            uploaded_by_user=self._user,
        ).first()
        return upload

    def clean(self) -> dict[str, Any]:
        cleaned_data: dict[str, Any] = super().clean()
        upload = self._pending_upload()
        if upload is None:
            return cleaned_data
        data = read_svg(upload.file)
        if data is not None:
            _, refusal = check_svg(data, cleaned_data.get("collection"))
            if refusal is not None:
                self.add_error("collection" if "collection" in self.fields else None, refusal)
        return cleaned_data
