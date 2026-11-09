"""The Wagtail image form base (`WAGTAILIMAGES_IMAGE_FORM_BASE`), THM.12.

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
from wagtail.images.forms import BaseImageForm
from wagtail.models import UploadedFile

from .models import MediaImage, check_svg, read_svg

# The prefix Wagtail gives the step-2 form: `edit_upload_form_prefix` and the upload's id.
_UPLOAD_PREFIX = re.compile(r"uploaded-image-(\d+)")


class MediaImageForm(BaseImageForm):  # type: ignore[misc]  # Wagtail is untyped
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._user = kwargs.get("user")
        super().__init__(*args, **kwargs)

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
