"""Hooks of the fitment app: the summary on the public product page (THM.7 part 3)."""

from wagtail import hooks

from apps.catalog.product_pages import FITMENT_HOOK

from .product_summary import summary

hooks.register(FITMENT_HOOK, summary)
