"""THM.18: a product with no image shows a decorative placeholder in its card.

Done when: "A card without an image renders the placeholder with an empty alt and the same image
class as a real image, under ride and the base theme; a card with an image never shows it".
Both card templates are rendered directly: the base include through the loader, the ride override
from its file, with the same context.
"""

from pathlib import Path
from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.conf import settings
from django.template import engines
from django.template.loader import render_to_string

pytestmark = pytest.mark.django_db

ROOT = Path(settings.BASE_DIR)
RIDE_CARD = ROOT / "themes/ride/templates/catalog/includes/product_card.html"


def _card(**overrides: Any) -> dict[str, Any]:
    card = {"title": "Oak lamp", "url": "/p/oak-lamp/", "price": None, "image": None}
    card.update(overrides)
    return card


def _base(card: dict[str, Any]) -> BeautifulSoup:
    html = render_to_string("catalog/includes/product_card.html", {"card": card, "currency": "EUR"})
    return BeautifulSoup(html, "html.parser")


def _ride(card: dict[str, Any]) -> BeautifulSoup:
    template = engines["django"].from_string(RIDE_CARD.read_text(encoding="utf-8"))
    return BeautifulSoup(template.render({"card": card, "currency": "EUR"}), "html.parser")


def test_a_base_card_without_an_image_shows_the_decorative_placeholder() -> None:
    img = _base(_card()).find("img")
    assert img is not None
    assert img["alt"] == ""
    assert "product-card-placeholder" in img["class"]
    assert str(img["src"]).endswith("catalog/product-placeholder.svg")


def test_a_ride_card_without_an_image_shows_the_placeholder_as_a_card_image() -> None:
    img = _ride(_card()).find("img")
    assert img is not None
    assert img["alt"] == ""
    assert {"card-img-top", "product-card-placeholder"} <= set(img["class"])
    assert str(img["src"]).endswith("themes/ride/images/product-placeholder.svg")


@pytest.mark.parametrize("render", [_base, _ride])
def test_a_card_with_an_image_url_never_shows_the_placeholder(render: Any) -> None:
    html = render(_card(image_url="/media/oak.webp", image_alt="An oak lamp"))
    images = html.find_all("img")
    assert [img["src"] for img in images] == ["/media/oak.webp"]
    assert not html.select(".product-card-placeholder")


def test_both_placeholders_exist() -> None:
    assert (ROOT / "apps/catalog/static/catalog/product-placeholder.svg").is_file()
    assert (ROOT / "themes/ride/static/themes/ride/images/product-placeholder.svg").is_file()
