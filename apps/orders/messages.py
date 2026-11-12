"""Customer-facing messages of the order flow (translated in the Italian catalog)."""

from django.utils.translation import gettext_lazy as _

CLOSED_SITE = _(
    "We are not taking new orders right now. Nothing was charged. Please try again later."
)
CLOSED_VENDOR = _(
    "One of the shops in your cart cannot take orders right now. Nothing was charged. "
    "Remove its items or try again later."
)
