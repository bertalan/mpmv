"""The upload form of the vendor's image library (EVT.4 part 2, CMS.8)."""

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.models import ImageLicence
from apps.core.nfc_forms import NFCForm


class EventImageForm(NFCForm):
    """The upload form of the shop's image library: the file and its licence data (CMS.8).
    Never a collection: the image goes into the shop's own."""

    nfc_exact_fields = ("permission_ref",)  # matched byte for byte (I18N.10)

    file = forms.FileField(
        label=_("File"),
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/png,image/jpeg,image/webp,image/gif"}
        ),
        help_text=_(
            "PNG, JPEG, WebP or GIF (SVG is not accepted). The image is checked and saved again "
            "without its hidden data. Required."
        ),
    )
    title = forms.CharField(
        label=_("Title"),
        max_length=255,
        required=False,
        help_text=_("A name for the library, for example Our stand 2027. Optional."),
    )
    licence = forms.ModelChoiceField(
        queryset=ImageLicence.objects.order_by("name"),
        label=_("Licence"),
        empty_label=_("Choose a licence"),
        help_text=_(
            "Under which licence you may use the image. Your own photo is Own work. Required."
        ),
    )
    author = forms.CharField(
        label=_("Author"),
        max_length=200,
        help_text=_("Who made the picture or holds its rights. Required."),
    )
    source_url = forms.URLField(
        label=_("Source address"),
        required=False,
        max_length=500,
        help_text=_("Where the image comes from, starting with https://. Some licences need it."),
    )
    credit_text = forms.CharField(
        label=_("Credit line"),
        max_length=250,
        required=False,
        help_text=_("Optional: replaces the credit shown under the image."),
    )
    permission_ref = forms.CharField(
        label=_("Permission reference"),
        max_length=200,
        required=False,
        help_text=_("For a licence that needs a written permission: where it is kept."),
    )
