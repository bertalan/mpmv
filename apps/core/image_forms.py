"""The Wagtail image form base (`WAGTAILIMAGES_IMAGE_FORM_BASE`), SEC.2.

Wagtail's upload field reads the image size through Willow, which decodes some formats fully,
and it only expects a decompression-bomb error there: a truncated or corrupt file raises
`OSError` (or `SyntaxError`, `ValueError`...) out of the form, a server error instead of a
refusal. `SafeImageField` turns any such failure into the field's "not a valid image" error.
The content checks themselves are `MediaImage.clean` (apps.core.uploads), on every path.
"""

from typing import Any

from django.core.exceptions import ValidationError
from wagtail.images.fields import WagtailImageField
from wagtail.images.forms import BaseImageForm


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


class ImageForm(BaseImageForm):  # type: ignore[misc]  # Wagtail is untyped
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields.get("file")
        if field is not None and type(field) is WagtailImageField:
            # Same field, safer parsing: keeps its widget, label, limits and messages.
            field.__class__ = SafeImageField
