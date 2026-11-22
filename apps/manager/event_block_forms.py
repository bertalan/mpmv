"""The vendor's block forms (EVT.4 part 2): one small form per vendor-safe block.

Each form builds the raw StreamField value of its block (`raw_value()`) and fills itself from a
stored value (`initial_for`). The choosers offer the shop's own images, products and events only;
the `save_event_draft` checks (EVT.3) run again on every save, so a crafted request gains nothing.
Only the blocks of `engine.VENDOR_SAFE_BLOCKS` have a form: the product grid has no source field,
the service forces the shop itself.
"""

import uuid
from collections.abc import Mapping
from typing import Any, ClassVar, cast

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.cms.engine import NEUTRAL, PRODUCT_GRID_DEFAULT, PRODUCT_GRID_MAX
from apps.core.models import MediaImage
from apps.core.nfc_forms import NFCForm
from apps.events import engine, services
from apps.events.models import EventPage
from apps.vendors.models import Vendor

from .event_fields import product_label


class _ImageChoice(forms.ModelChoiceField):  # type: ignore[type-arg]
    def label_from_instance(self, obj: Any) -> str:
        return str(obj.title)


def _item(value: Any) -> dict[str, Any]:
    return {"type": "item", "value": value, "id": str(uuid.uuid4())}


def _unwrap(item: Any) -> Any:
    """The value of a list item, plain or in Wagtail's `{"type": "item", "value": ...}` wrapper."""
    if isinstance(item, Mapping) and "value" in item and "type" in item:
        return item["value"]
    return item


class BlockForm(NFCForm):
    """Base of the block forms: `vendor` scopes every chooser."""

    kind: ClassVar[str]
    block_label: ClassVar[Any]

    def __init__(self, *args: Any, vendor: Vendor, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.vendor = vendor
        self.setup()

    def setup(self) -> None:
        """Scope the choosers to the vendor's own rows."""

    def raw_value(self) -> Any:
        raise NotImplementedError

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        raise NotImplementedError

    def image_field(self, *, required: bool, label: Any, help_text: Any = "") -> _ImageChoice:
        return _ImageChoice(
            queryset=services.vendor_image_library(self.vendor),
            required=required,
            label=label,
            help_text=help_text,
            empty_label=_("Choose an image") if required else _("No image"),
        )


_IMAGE_HELP = _(
    "Pick one of your own images. Upload new ones in the image library first. Required."
)
_ALT_HELP = _(
    "Describe the picture in a few words, for people who cannot see it. Required with a picture."
)


class ParagraphForm(BlockForm):
    kind = "paragraph"
    block_label = _("Text")

    text = forms.CharField(
        label=_("Text"),
        widget=forms.Textarea(attrs={"rows": 8}),
        max_length=engine.MAX_TEXT_LENGTH,
        help_text=_(
            "Write plain text: a blank line starts a new paragraph. You can also use simple "
            "HTML (headings h2 and h3, bold, italic, lists and links). Required."
        ),
    )

    def raw_value(self) -> str:
        return engine.paragraph_html(self.cleaned_data["text"])

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        return {"text": engine.paragraph_text(str(value or ""))}


class FigureForm(BlockForm):
    kind = "figure"
    block_label = _("Image")

    image: forms.ModelChoiceField[MediaImage] = forms.ModelChoiceField(
        queryset=MediaImage.objects.none(), label=_("Image"), help_text=_IMAGE_HELP
    )
    alt_text = forms.CharField(label=_("Alt text"), max_length=250, help_text=_ALT_HELP)
    caption = forms.CharField(
        label=_("Caption"),
        max_length=250,
        required=False,
        help_text=_("A short line under the picture. Leave it empty for none."),
    )

    def setup(self) -> None:
        self.fields["image"] = self.image_field(
            required=True, label=_("Image"), help_text=_IMAGE_HELP
        )

    def raw_value(self) -> dict[str, Any]:
        data = self.cleaned_data
        return {
            "image": data["image"].pk,
            "alt_text": data["alt_text"],
            "caption": data["caption"],
        }

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        return {name: value.get(name) for name in ("image", "alt_text", "caption")}


class GalleryForm(BlockForm):
    kind = "gallery"
    block_label = _("Gallery")

    def setup(self) -> None:
        for slot in range(1, engine.GALLERY_SLOTS + 1):
            self.fields[f"image_{slot}"] = self.image_field(
                required=False,
                label=_("Image %(number)d") % {"number": slot},
                help_text=_("Leave the slot empty to skip it.") if slot == 1 else "",
            )
            self.fields[f"alt_text_{slot}"] = forms.CharField(
                label=_("Alt text %(number)d") % {"number": slot}, max_length=250, required=False
            )
            self.fields[f"caption_{slot}"] = forms.CharField(
                label=_("Caption %(number)d") % {"number": slot}, max_length=250, required=False
            )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        used = 0
        for slot in range(1, engine.GALLERY_SLOTS + 1):
            if data.get(f"image_{slot}") is None:
                continue
            used += 1
            if not (data.get(f"alt_text_{slot}") or "").strip():
                self.add_error(f"alt_text_{slot}", _("Describe the image."))
        if not used and not self.errors:
            self.add_error(None, _("Choose at least one image."))
        return data

    def raw_value(self) -> dict[str, Any]:
        data = self.cleaned_data
        images = [
            _item(
                {
                    "image": data[f"image_{slot}"].pk,
                    "alt_text": data[f"alt_text_{slot}"],
                    "caption": data[f"caption_{slot}"],
                    "language": NEUTRAL,
                }
            )
            for slot in range(1, engine.GALLERY_SLOTS + 1)
            if data.get(f"image_{slot}") is not None
        ]
        return {"images": images}

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        initial: dict[str, Any] = {}
        for slot, item in enumerate(value.get("images") or [], start=1):
            if slot > engine.GALLERY_SLOTS:
                break
            image = _unwrap(item)
            initial[f"image_{slot}"] = image.get("image")
            initial[f"alt_text_{slot}"] = image.get("alt_text")
            initial[f"caption_{slot}"] = image.get("caption")
        return initial


class CtaForm(BlockForm):
    kind = "cta"
    block_label = _("Call to action")

    heading = forms.CharField(
        label=_("Heading"),
        max_length=120,
        help_text=_("The invitation, for example Visit our workshop. Required."),
    )
    text = forms.CharField(
        label=_("Text"),
        max_length=400,
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("A sentence or two that says why to click. Leave it empty for none."),
    )
    label = forms.CharField(
        label=_("Button text"),
        max_length=100,
        help_text=_("The words visitors click, for example Read more. Required."),
    )
    url = forms.URLField(
        label=_("Web address"),
        required=False,
        max_length=500,
        help_text=_("An address starting with https://. Fill in this or one of your events."),
    )
    page: forms.ModelChoiceField[EventPage] = forms.ModelChoiceField(
        queryset=EventPage.objects.none(),
        label=_("One of your events"),
        required=False,
        empty_label=_("No event"),
        help_text=_("Link to one of your own events instead of a web address."),
    )

    def setup(self) -> None:
        self.fields["page"].queryset = services.vendor_events(self.vendor)  # type: ignore[attr-defined]

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        url, page = data.get("url"), data.get("page")
        if url and page is not None:
            self.add_error(None, _("Choose an event or give a web address, not both."))
        elif not url and page is None and not self.errors:
            self.add_error(None, _("Choose an event or give a web address."))
        elif url:
            problem = engine.cta_url_problem(str(url))
            if problem is not None:
                self.add_error("url", problem)
        return data

    def raw_value(self) -> dict[str, Any]:
        data = self.cleaned_data
        page = data.get("page")
        return {
            "heading": data["heading"],
            "text": data["text"],
            "link": {
                "page": page.pk if page is not None else None,
                "url": data.get("url") or "",
                "label": data["label"],
            },
        }

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        link = value.get("link") or {}
        return {
            "heading": value.get("heading"),
            "text": value.get("text"),
            "label": link.get("label"),
            "url": link.get("url"),
            "page": link.get("page"),
        }


class FaqForm(BlockForm):
    kind = "faq"
    block_label = _("Questions and answers")

    heading = forms.CharField(
        label=_("Heading"),
        max_length=120,
        required=False,
        help_text=_("A title above the questions. Leave it empty for none."),
    )

    def setup(self) -> None:
        for slot in range(1, engine.FAQ_SLOTS + 1):
            self.fields[f"question_{slot}"] = forms.CharField(
                label=_("Question %(number)d") % {"number": slot},
                max_length=250,
                required=False,
                help_text=_("Leave the slot empty to skip it.") if slot == 1 else "",
            )
            self.fields[f"answer_{slot}"] = forms.CharField(
                label=_("Answer %(number)d") % {"number": slot},
                required=False,
                max_length=engine.MAX_TEXT_LENGTH,
                widget=forms.Textarea(attrs={"rows": 3}),
            )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        filled = 0
        for slot in range(1, engine.FAQ_SLOTS + 1):
            question = (data.get(f"question_{slot}") or "").strip()
            answer = (data.get(f"answer_{slot}") or "").strip()
            if question and answer:
                filled += 1
            elif question:
                self.add_error(f"answer_{slot}", _("Write the answer."))
            elif answer:
                self.add_error(f"question_{slot}", _("Write the question."))
        if not filled and not self.errors:
            self.add_error(None, _("Add at least one question with its answer."))
        return data

    def raw_value(self) -> dict[str, Any]:
        data = self.cleaned_data
        items = [
            _item(
                {
                    "question": data[f"question_{slot}"].strip(),
                    "answer": engine.paragraph_html(data[f"answer_{slot}"]),
                }
            )
            for slot in range(1, engine.FAQ_SLOTS + 1)
            if (data.get(f"question_{slot}") or "").strip()
            and (data.get(f"answer_{slot}") or "").strip()
        ]
        return {"heading": data["heading"], "items": items}

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        initial: dict[str, Any] = {"heading": value.get("heading")}
        for slot, item in enumerate(value.get("items") or [], start=1):
            if slot > engine.FAQ_SLOTS:
                break
            pair = _unwrap(item)
            initial[f"question_{slot}"] = pair.get("question")
            initial[f"answer_{slot}"] = engine.paragraph_text(str(pair.get("answer") or ""))
        return initial


class ProductCardsForm(BlockForm):
    kind = "product_cards"
    block_label = _("Product cards")

    heading = forms.CharField(
        label=_("Heading"),
        max_length=120,
        required=False,
        help_text=_("A title above the products. Leave it empty for none."),
    )
    layout = forms.ChoiceField(
        label=_("Layout"),
        choices=[("grid", _("Grid")), ("row", _("Row"))],
        initial="grid",
        help_text=_("A grid, or one row that scrolls sideways."),
    )
    products: forms.ModelMultipleChoiceField[Product] = forms.ModelMultipleChoiceField(
        queryset=Product.objects.none(),
        label=_("Products"),
        widget=forms.CheckboxSelectMultiple,
        help_text=_("Your own products, up to 12. Required."),
    )

    def setup(self) -> None:
        field = self.fields["products"]
        field.queryset = services.vendor_products(self.vendor)  # type: ignore[attr-defined]
        field.label_from_instance = product_label  # type: ignore[attr-defined]

    def clean_products(self) -> Any:
        products = self.cleaned_data["products"]
        if len(products) > 12:
            raise forms.ValidationError(_("Choose at most 12 products."))
        return products

    def raw_value(self) -> dict[str, Any]:
        data = self.cleaned_data
        chosen = {product.pk: product for product in data["products"]}
        order = [
            int(pk)
            for pk in cast(Any, self.data).getlist(self.add_prefix("products"))
            if int(pk) in chosen
        ]
        return {
            "heading": data["heading"],
            "layout": data["layout"],
            "products": [_item(pk) for pk in dict.fromkeys(order)],
        }

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        return {
            "heading": value.get("heading"),
            "layout": value.get("layout") or "grid",
            "products": [_unwrap(item) for item in value.get("products") or []],
        }


class ProductGridForm(BlockForm):
    kind = "product_grid"
    block_label = _("Product grid")

    heading = forms.CharField(
        label=_("Heading"),
        max_length=120,
        required=False,
        help_text=_("A title above the products. Leave it empty to use the name of your shop."),
    )
    limit = forms.IntegerField(
        label=_("Number of products"),
        min_value=1,
        max_value=PRODUCT_GRID_MAX,
        initial=PRODUCT_GRID_DEFAULT,
        help_text=_("How many of your newest products to show."),
    )
    see_all = forms.BooleanField(
        label=_("Link to the full listing"),
        required=False,
        initial=True,
        help_text=_("Tick it to add a See all link under the products."),
    )

    def raw_value(self) -> dict[str, Any]:
        data = self.cleaned_data
        return {
            "heading": data["heading"],
            # The source is always the shop itself: the vendor cannot name another.
            "source": [{"type": "vendor", "value": self.vendor.pk, "id": str(uuid.uuid4())}],
            "limit": data["limit"],
            "see_all": bool(data["see_all"]),
        }

    @classmethod
    def initial_for(cls, value: Any) -> dict[str, Any]:
        return {name: value.get(name) for name in ("heading", "limit", "see_all")}


FORMS: dict[str, type[BlockForm]] = {
    form.kind: form
    for form in (
        ParagraphForm,
        FigureForm,
        GalleryForm,
        CtaForm,
        FaqForm,
        ProductCardsForm,
        ProductGridForm,
    )
}
assert set(FORMS) == set(engine.VENDOR_SAFE_BLOCKS)  # noqa: S101  # a block without a form
