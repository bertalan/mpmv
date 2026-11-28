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
from django.utils.translation import gettext as _
from wagtail.images.fields import WagtailImageField
from wagtail.images.forms import BaseImageForm
from wagtail.models import UploadedFile

from . import media_ai_origin
from .ai_origin import AI_NONE, is_lowering
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
        origin = self.fields.get("ai_origin")
        if origin is not None:
            origin.required = False  # CMP.8: a form that leaves it out keeps the stored value
        # The stored value (none for a new upload): the column is written by the audited service
        # in `save`, never by the form (CMP.8: editors raise, staff lower).
        stored = self.instance.ai_origin if self.instance.pk else AI_NONE
        self._stored_ai_origin: str = stored or AI_NONE

    def _post_clean(self) -> None:
        super()._post_clean()
        if self.errors and self.original_file and self.instance.file is not self.original_file:
            # A refused replacement (SEC.2): the edit page shows the form again with a preview
            # rendition of `instance.file`, which Django has already set to the refused upload.
            # The stored image is put back, so no rendition is ever made from a refused file.
            self.instance.file = self.original_file

    def save(self, commit: bool = True) -> Any:
        """Store the image with its stored AI origin, then change the origin through
        `media.set_media_ai_origin`, which audits it (CMP.8): a new upload that names an origin
        is an audited change from none, like an edit."""
        wanted = self.cleaned_data.get("ai_origin") if "ai_origin" in self.fields else None
        if wanted is not None:
            self.instance.ai_origin = self._stored_ai_origin
        image = super().save(commit=commit)
        if wanted is not None and wanted != self._stored_ai_origin:
            if commit:
                media_ai_origin.set_origin(image, wanted, self._user)
            else:
                save_m2m: Any = self.save_m2m  # type: ignore[has-type]

                def save_m2m_and_origin() -> None:
                    save_m2m()
                    media_ai_origin.set_origin(image, wanted, self._user)

                self.save_m2m = save_m2m_and_origin
        return image

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
        if "ai_origin" in self.fields and not cleaned_data.get("ai_origin"):
            cleaned_data["ai_origin"] = self._stored_ai_origin
        wanted = cleaned_data.get("ai_origin")
        if (
            wanted
            and is_lowering(self._stored_ai_origin, wanted)
            and not media_ai_origin.may_lower(self._user)
        ):
            self.add_error(
                "ai_origin",
                _(
                    "Only platform staff may lower an image's AI origin. "
                    "Please keep the current value or ask the platform team."
                ),
            )
        upload = self._pending_upload()
        if upload is None:
            return cleaned_data
        data = read_svg(upload.file)
        if data is not None:
            _safe, refusal = check_svg(data, cleaned_data.get("collection"))
            if refusal is not None:
                self.add_error("collection" if "collection" in self.fields else None, refusal)
        return cleaned_data
