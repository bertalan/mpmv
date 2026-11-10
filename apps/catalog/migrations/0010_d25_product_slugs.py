"""D-25 (CAT.5b): reslug existing products to `{title}`, without the language suffix.

Runs on the historical models only, with a frozen private copy of the pure slug functions, so a
later migration that adds a column cannot break a fresh `migrate`. The replaced `{title}-{lang}`
and legacy `{title}-{lang}-{sku}` slugs stay in SlugHistory and answer a single 301 to the new
slug. Each reslug writes the same `catalog.product.slug_changed` audit entry as
`services.sync_product_slug`. A rerun changes nothing. Reverse is a no-op: the old slugs remain in
SlugHistory and a rollback keeps the new slugs, which are valid; nothing is lost.

Dry run and audit path: `manage.py rebuild_product_slugs --dry-run` (docs/dev/release.md).
"""

import re
import unicodedata
from collections.abc import Container
from typing import Any

from django.db import migrations
from django.utils import timezone

# --- Frozen private copy of the D-25 slug rules (as in `apps/catalog/slugs.py` on 2026-11-10).
# A later change to slugs.py must not change what this migration did; a test pins the copy against
# slugs.py on sample titles, so a deliberate change there shows up and is decided on purpose.
SLUG_MAX_LENGTH = 80
FALLBACK_TITLE = "product"

_TRANSLITERATION = str.maketrans(
    {
        "ß": "ss",
        "ẞ": "SS",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "TH",
        "ł": "l",
        "Ł": "L",
        "\u0131": "i",  # dotless i
    }
)
_APOSTROPHES = re.compile("['\u2018\u2019`]")  # straight, curly and grave apostrophes
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text.translate(_TRANSLITERATION))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    return _NOT_SLUG.sub("-", _APOSTROPHES.sub("", ascii_text).lower()).strip("-")


def _compose(title_part: str, tail: str, max_len: int) -> str:
    room = max_len - len(tail)
    if room < 1:
        raise ValueError(f"max_len={max_len} leaves no room for the title before {tail!r}")
    return f"{title_part[:room].rstrip('-') or FALLBACK_TITLE[:room]}{tail}"


def unique_product_slug(title: str, taken: Container[str], max_len: int = SLUG_MAX_LENGTH) -> str:
    """`build_product_slug`, with -2, -3… appended while the slug is in `taken` (the slugs of
    the other products in the same language)."""
    title_part = _slugify(title) or FALLBACK_TITLE
    slug = _compose(title_part, "", max_len)
    number = 1
    while slug in taken:
        number += 1
        slug = _compose(title_part, f"-{number}", max_len)
    return slug


ACTION = "catalog.product.slug_changed"
ACTOR = "migration:catalog.0010_d25_product_slugs"


class _Taken:
    """The current and old slugs of the other products in one language."""

    def __init__(self, translations: Any, history: Any, language: str, product_id: int) -> None:
        self.translations, self.history = translations, history
        self.language, self.product_id = language, product_id

    def __contains__(self, slug: object) -> bool:
        return bool(
            self.translations.filter(language=self.language, slug=slug)
            .exclude(product_id=self.product_id)
            .exists()
            or self.history.objects.filter(language=self.language, slug=slug)
            .exclude(product_id=self.product_id)
            .exists()
        )


def reslug_products(
    apps: Any, schema_editor: Any, *, dry_run: bool = False
) -> list[tuple[Any, ...]]:
    """Reslug every translation; returns (translation id, old slug, new slug) for each change."""
    translation_model = apps.get_model("catalog", "ProductTranslation")
    history_model = apps.get_model("catalog", "SlugHistory")
    audit_model = apps.get_model("core", "AuditLog")
    changed: list[tuple[Any, ...]] = []
    translations = translation_model.objects.select_related("product__vendor").order_by("pk")
    if not translations.exists():
        return changed

    for translation in translations.iterator():
        product, language, old_slug = translation.product, translation.language, translation.slug

        taken = _Taken(translation_model.objects, history_model, language, product.pk)
        new_slug = unique_product_slug(translation.title, taken=taken)
        if new_slug == old_slug:
            continue
        changed.append((translation.pk, old_slug, new_slug))
        if dry_run:
            continue
        # Going back to one of the product's own old slugs takes it out of the history.
        history_model.objects.filter(product=product, language=language, slug=new_slug).delete()
        if old_slug:
            history_model.objects.get_or_create(product=product, language=language, slug=old_slug)
        translation_model.objects.filter(pk=translation.pk).update(
            slug=new_slug, updated_at=timezone.now()
        )
        if old_slug:
            vendor = str(product.vendor.public_id)
            audit_model.objects.create(
                actor_label=ACTOR,
                source="command",
                action=ACTION,
                target_model="catalog.product",
                target_key=str(product.public_id),
                before={"vendor": vendor, "language": language, "slug": old_slug},
                after={"vendor": vendor, "language": language, "slug": new_slug},
                vendor_key=vendor[:64],
            )
    return changed


def forwards(apps: Any, schema_editor: Any) -> None:
    reslug_products(apps, schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0009_redirect"),
        ("core", "0017_operator_alert_emails"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
