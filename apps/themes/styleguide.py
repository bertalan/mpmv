"""Sample data for the staff-only styleguide page (THM.4, spec 14 §14.9).

The page shows every component of the active theme in every state. These forms carry one field
of each control; the view renders them unbound, disabled and bound with invalid data, each with
its own prefix so that the ids on the page never repeat. The product tiles come in every state:
with and without an image, on sale, new, out of stock and with no price. Part 3/3 adds the
breadcrumb trails, numbered pages and tabs, the samples of the rest of the library, and
`COMPONENTS`, the §14.9 list mirrored.
"""

from datetime import UTC, datetime
from typing import Any, NamedTuple

from django import forms
from django.templatetags.static import static
from django.utils.translation import gettext_lazy as _

COUNTRIES = [("", _("Choose a country")), ("at", _("Austria")), ("it", _("Italy"))]
DELIVERY = [("standard", _("Standard")), ("express", _("Express"))]
EXTRAS = [("gift", _("Gift wrap")), ("card", _("Greeting card"))]


class TextFieldsForm(forms.Form):
    """Text-like controls: text, email, textarea, select and file."""

    full_name = forms.CharField(
        label=_("Full name"),
        widget=forms.TextInput(attrs={"autocomplete": "name"}),
    )
    email = forms.EmailField(
        label=_("Email"),
        help_text=_("We send the order confirmation here."),
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    country = forms.ChoiceField(label=_("Country"), choices=COUNTRIES)
    message = forms.CharField(
        label=_("Message to the vendor"), widget=forms.Textarea(attrs={"rows": 3}), required=False
    )
    receipt = forms.FileField(
        label=_("Receipt"), help_text=_("PDF or image, 5 MB at most."), required=False
    )


class ChoicesForm(forms.Form):
    """Choice controls: a single checkbox, a radio group and a checkbox group."""

    delivery = forms.ChoiceField(label=_("Delivery"), choices=DELIVERY, widget=forms.RadioSelect)
    extras = forms.MultipleChoiceField(
        label=_("Extras"),
        choices=EXTRAS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        help_text=_("Optional, charged at checkout."),
    )
    terms = forms.BooleanField(label=_("I accept the terms of sale"))

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if data.get("delivery") == "express" and "gift" in data.get("extras", []):
            raise forms.ValidationError(_("Gift wrap is not available with express delivery."))
        return data


# Values that fail validation, so the invalid state shows a field error on every control and
# an error of the whole form.
INVALID_TEXT = {"invalid-text-email": "not-an-email", "invalid-text-message": "Hello"}
INVALID_CHOICES = {"invalid-choices-extras": ["gift"], "invalid-choices-delivery": "express"}


def _disabled(form: forms.Form) -> forms.Form:
    for field in form.fields.values():
        field.disabled = True
    return form


def sample_forms() -> dict[str, forms.Form]:
    """Every form of the page, by state: default, disabled, invalid (bound and validated)."""
    invalid_text = TextFieldsForm(data=INVALID_TEXT, prefix="invalid-text")
    invalid_choices = ChoicesForm(data=INVALID_CHOICES, prefix="invalid-choices")
    invalid_text.is_valid()
    invalid_choices.is_valid()
    return {
        "text": TextFieldsForm(prefix="text"),
        "text_disabled": _disabled(
            TextFieldsForm(prefix="disabled-text", initial={"full_name": "Ada Rossi"})
        ),
        "text_invalid": invalid_text,
        "choices": ChoicesForm(prefix="choices"),
        "choices_disabled": _disabled(
            ChoicesForm(prefix="disabled-choices", initial={"delivery": "standard"})
        ),
        "choices_invalid": invalid_choices,
    }


def sample_products() -> list[dict[str, Any]]:
    """Product tiles in every state. The image is a same-origin static file (CSP img-src)."""
    image = static("styleguide/sample-product.svg")
    return [
        {
            "name": _("Touring jacket"),
            "url": "#sg-tiles",
            "image_url": image,
            "image_alt": _("Grey placeholder shape"),
            "price": "€ 189.00",
            "vendor": "Vendor A",
            "badges": ["new"],
        },
        {
            "name": _("Rain cover"),
            "url": "#sg-tiles",
            "image_url": image,
            "image_alt": "",
            "price": "€ 39.00",
            "price_before": "€ 49.00",
            "vendor": "Vendor B",
            "badges": ["sale"],
        },
        {
            "name": _("Winter gloves"),
            "url": "#sg-tiles",
            "image_url": image,
            "image_alt": "",
            "price": "€ 59.00",
            "vendor": "Vendor C",
            "badges": ["out_of_stock"],
        },
        {
            "name": _("Made-to-measure seat"),
            "url": "#sg-tiles",
            "image_url": "",
            "price": "",
            "vendor": "Vendor D",
            "badges": ["on_request"],
        },
    ]


# --- Every component of spec 14 §14.9, by the name the spec gives it (a unit test reads the
# spec's list and compares). Each one maps to the styleguide section that shows it, its template
# and a marker of its markup that the integration test looks for in that section.


class Shown(NamedTuple):
    section: str
    template: str
    marker: str


COMPONENTS: dict[str, Shown] = {
    "skip link": Shown("sg-frame", "components/skip_link.html", 'class="skip-link"'),
    "header and disclosure navigation": Shown(
        "sg-frame", "components/header.html", 'class="site-header"'
    ),
    "language switcher": Shown(
        "sg-frame", "components/language_switcher.html", 'class="language-switcher"'
    ),
    "footer": Shown("sg-frame", "components/footer.html", 'class="site-footer"'),
    "search form": Shown("sg-search", "components/search_form.html", 'class="search-form'),
    "mini-cart status": Shown("sg-mini-cart", "components/mini_cart.html", 'class="mini-cart"'),
    "breadcrumb": Shown("sg-breadcrumbs", "components/breadcrumb.html", 'class="breadcrumb"'),
    "buttons": Shown("sg-buttons", "components/button.html", 'class="button'),
    "form fields": Shown("sg-text", "components/field.html", 'class="field'),
    "alert": Shown("sg-alerts", "components/alert.html", 'class="alert'),
    "badge": Shown("sg-badges", "components/badge.html", 'class="badge'),
    "price": Shown("sg-price", "components/price.html", 'class="price"'),
    "rating": Shown("sg-rating", "components/rating.html", 'class="rating"'),
    "product card": Shown("sg-tiles", "components/product_tile.html", 'class="product-tile'),
    "product grid": Shown("sg-tiles", "components/product_grid.html", 'class="product-grid"'),
    "pagination": Shown("sg-pagination", "components/pagination.html", 'class="pagination"'),
    "accordion": Shown("sg-accordion", "components/accordion.html", 'class="accordion"'),
    "tabs": Shown("sg-tabs", "components/tabs.html", 'class="tabs"'),
    "dialog": Shown("sg-dialog", "components/dialog.html", 'class="dialog"'),
    "scroll-snap carousel with buttons": Shown(
        "sg-carousel", "components/carousel.html", 'class="carousel"'
    ),
    "gallery": Shown("sg-gallery", "components/gallery.html", 'class="gallery"'),
    "quantity stepper": Shown(
        "sg-quantity", "components/quantity_stepper.html", 'class="quantity"'
    ),
    "cart line": Shown("sg-cart", "components/cart_line.html", 'class="cart-line'),
    "order summary": Shown("sg-cart", "components/order_summary.html", 'class="order-summary"'),
    "step indicator": Shown("sg-steps", "components/step_indicator.html", 'class="steps"'),
    "empty state": Shown("sg-empty", "components/empty_state.html", 'class="empty-state"'),
    "live-region toast": Shown("sg-toasts", "components/toast.html", 'class="toast-region"'),
    "responsive table": Shown("sg-tables", "components/table.html", 'class="table-scroll"'),
    "hero": Shown("sg-hero", "components/hero.html", 'class="hero'),
    "CTA band": Shown("sg-cta", "components/cta_band.html", 'class="cta-band'),
    "logo strip": Shown("sg-logos", "components/logo_strip.html", 'class="logo-strip"'),
    "FAQ": Shown("sg-faq", "components/faq.html", 'class="faq"'),
    "launch countdown": Shown(
        "sg-countdown", "components/launch_countdown.html", 'class="launch-countdown'
    ),
    # CMS.8 ships the credit in the core app; the styleguide shows it with the active theme.
    "image credit": Shown(
        "sg-credits", "core/components/image_credit.html", 'class="image-credit"'
    ),
}


def sample_breadcrumbs() -> dict[str, list[dict[str, Any]]]:
    """Breadcrumb trails: a deep product page and a first-level page."""
    return {
        "deep": [
            {"label": _("Home"), "url": "#sg-breadcrumbs"},
            {"label": _("Clothing"), "url": "#sg-breadcrumbs"},
            {"label": _("Jackets"), "url": "#sg-breadcrumbs"},
            {"label": _("Touring jacket"), "url": "#sg-breadcrumbs"},
        ],
        "short": [
            {"label": _("Home"), "url": "#sg-breadcrumbs"},
            {"label": _("About us"), "url": "#sg-breadcrumbs"},
        ],
    }


def sample_pages(current: int, count: int) -> list[dict[str, Any] | None]:
    """Numbered page links around `current`: first, last, the neighbours, gaps as None."""
    shown = sorted({1, count, *range(max(1, current - 1), min(count, current + 1) + 1)})
    pages: list[dict[str, Any] | None] = []
    for number in shown:
        previous = pages[-1] if pages else None
        if previous is not None and number - previous["number"] > 1:
            pages.append(None)
        pages.append({"number": number, "url": f"?page={number}#sg-pagination"})
    return pages


def sample_in_page_links() -> dict[str, list[dict[str, Any]]]:
    """The skip link to the filters and an "On this page" list of the styleguide sections."""
    return {
        "skip": [{"label": _("Skip to filters"), "url": "#sg-filters"}],
        "page": [
            {"label": _("Buttons"), "url": "#sg-buttons"},
            {"label": _("Product tiles"), "url": "#sg-tiles"},
            {"label": _("Pagination"), "url": "#sg-pagination"},
        ],
    }


def sample_tabs() -> list[dict[str, Any]]:
    """Short product sections for the tabs."""
    return [
        {"label": _("Description"), "content": _("A light jacket for long rides in spring.")},
        {"label": _("Size guide"), "content": _("Measure your chest and pick the next size up.")},
        {"label": _("Care"), "content": _("Wash at 30 degrees, never tumble dry.")},
    ]


def sample_library() -> dict[str, Any]:
    """Samples for the rest of the library: commerce, content and feedback components."""
    image = static("styleguide/sample-product.svg")
    return {
        "image": image,
        "accordion": [
            {"title": _("Shipping"), "content": _("Two to four working days across the EU.")},
            {"title": _("Returns"), "content": _("Within 14 days, free of charge."), "open": True},
            {"title": _("Warranty"), "content": _("Two years on every product.")},
        ],
        "faq": [
            {"question": _("Do you ship abroad?"), "answer": _("Yes, to every EU country.")},
            {"question": _("Can I pay by bank transfer?"), "answer": _("Yes, at checkout.")},
        ],
        "slides": [
            {"title": _("Touring jacket"), "text": "€ 189.00", "url": "#sg-carousel"},
            {"title": _("Rain cover"), "text": "€ 39.00", "url": "#sg-carousel"},
            {"title": _("Winter gloves"), "text": "€ 59.00", "url": "#sg-carousel"},
            {"title": _("Tank bag"), "text": "€ 79.00", "url": "#sg-carousel"},
            {"title": _("Helmet visor"), "text": "€ 29.00", "url": "#sg-carousel"},
        ],
        "images": [
            {"url": image, "alt": _("The jacket from the front")},
            {"url": image, "alt": _("The jacket from the side")},
            {"url": image, "alt": _("The collar, close up")},
        ],
        "summary": [
            {"label": _("Subtotal"), "amount": "€ 228.00"},
            {"label": _("Shipping, Vendor A"), "amount": "€ 9.00"},
            {"label": _("Discount"), "amount": "-€ 10.00"},
        ],
        "summary_pending": [
            {"label": _("Subtotal"), "amount": "€ 39.00"},
            {"label": _("Shipping"), "amount": _("Calculated at the next step")},
        ],
        "steps": [
            {"label": _("Cart"), "url": "#sg-steps"},
            {"label": _("Address"), "url": "#sg-steps"},
            {"label": _("Shipping"), "url": "#sg-steps"},
            {"label": _("Payment"), "url": "#sg-steps"},
        ],
        "columns": [
            {"label": _("Order")},
            {"label": _("Date")},
            {"label": _("Vendor")},
            {"label": _("Status")},
            {"label": _("Items"), "numeric": True},
            {"label": _("Total"), "numeric": True},
        ],
        "rows": [
            ["SAMPLE-1001", "2026-10-02", "Vendor A", _("Shipped"), "2", "€ 228.00"],
            ["SAMPLE-1002", "2026-10-14", "Vendor B", _("Paid"), "1", "€ 39.00"],
            ["SAMPLE-1003", "2026-11-01", "Vendor C", _("Cancelled"), "3", "€ 177.00"],
        ],
        "logos": [
            {"name": "Vendor A", "image_url": image, "url": "#sg-logos"},
            {"name": "Vendor B", "image_url": image, "url": "#sg-logos"},
            {"name": "Vendor C", "image_url": image},
        ],
        "launch": datetime(2030, 3, 1, 9, 0, tzinfo=UTC),
        "launched": datetime(2026, 3, 1, 9, 0, tzinfo=UTC),
        "credit_full": {
            "text": _("Photo by Ada Rossi"),
            "licence_name": "CC BY 4.0",
            "licence_url": "https://creativecommons.org/licenses/by/4.0/",
            "source_url": "#sg-credits",
        },
        "credit_plain": {"text": _("Own work, Vendor A"), "licence_name": "CC0 1.0"},
        "credits": [
            {"text": _("Photo by Ada Rossi"), "licence_name": "CC BY 4.0"},
            {"text": _("Own work, Vendor A"), "licence_name": "CC0 1.0"},
        ],
        "toasts": [_("Address saved.")],
    }
