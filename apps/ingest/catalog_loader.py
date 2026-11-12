"""MIG.3 part 1: load vendors, catalog and taxonomies from the intermediate format.

Called through `apps.ingest.services.load_legacy_catalog`. The input shape and the rules are in
`legacy_catalog` (pure). Identity is the natural key of each target row, so a reload finds what
is already there and skips it (a record seen before is never updated, spec 13 §13.3):

- vendor: `legacy_wp_user_id`; product and variant: vendor + SKU match key, checked against the
  translation group (two groups never merge on the SKU alone); category: parent + EN slug;
  taxonomy term: taxonomy + EN slug; brand: name; attribute: code; value: attribute + value.
- dry-run runs the real writes inside a transaction that is rolled back, so the ChangeSet and
  the report are exactly what a real run does, ids and constraints included.
- Existing legacy texts are loaded as published translations (D-18). An item with no text in a
  site language other than EN gets a `machine` placeholder row in that language: the row is the
  translation-queue entry (I18N.4 lists rows by status) and visitors see the EN fallback notice
  because only published rows are shown.

Left for follow-ups (listed in the report's `deferred`, never silently dropped): the per-vendor
and per-product commission (CommissionRule: VEN.2, MIG.4), the vendor VAT-regime profile (the
`tax` app, which ingest may not import), images (MIG.3 part 2), legacy slugs as `SlugHistory` and
redirects (MIG.6).
"""

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from apps.catalog import engine as catalog_engine
from apps.catalog import publishing
from apps.catalog import services as catalog_services
from apps.catalog.models import (
    Attribute,
    AttributeKind,
    AttributeTranslation,
    AttributeValue,
    AttributeValueTranslation,
    Brand,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Product,
    ProductAttribute,
    ProductKind,
    ProductStatus,
    ProductTranslation,
    SalesMode,
    StockPolicy,
    StockStatus,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
    Variant,
    VariantOption,
    VariantTranslation,
)
from apps.core import translations
from apps.core.audit import AuditContext
from apps.core.changeset import ChangeSet
from apps.core.models import TranslationStatus
from apps.core.services import audit_changes
from apps.vendors import services as vendor_services
from apps.vendors.models import Vendor, VendorTranslation

from . import legacy_catalog as rules

CREATED = "ingest.catalog_load.created"
PUBLISHED_STATUS = TranslationStatus.PUBLISHED
MACHINE_STATUS = TranslationStatus.MACHINE
Row = Mapping[str, Any]


@dataclass
class LoadReport:
    """What a catalog load did (or, on dry-run, would do)."""

    changes: ChangeSet = field(default_factory=ChangeSet)
    created: dict[str, int] = field(default_factory=dict)
    skipped_existing: int = 0
    # Records rejected with the reason; the load goes on with the others.
    problems: list[str] = field(default_factory=list)
    # Translation-queue entries: items migrated EN-only, one per missing language.
    translation_queue: list[str] = field(default_factory=list)
    # Published in the legacy shop but kept hidden here because a publication check blocks them.
    held_back: list[str] = field(default_factory=list)
    # Taxonomies and attributes no loaded product uses.
    dropped: list[str] = field(default_factory=list)
    # Trashed posts: only redirect sources (MIG.6), never loaded.
    redirect_sources: list[str] = field(default_factory=list)
    # Unpublished posts left out because the vendor did not choose to bring them (MIG.16).
    skipped_unpublished: list[str] = field(default_factory=list)
    # Data the later tasks load: {"commission": [...], "vat_regime": [...]}.
    deferred: dict[str, list[str]] = field(default_factory=dict)

    def count(self, model: str) -> None:
        self.created[model] = self.created.get(model, 0) + 1

    def summary(self) -> dict[str, Any]:
        return {
            "created": dict(self.created),
            "skipped_existing": self.skipped_existing,
            "problems": len(self.problems),
            "translation_queue": len(self.translation_queue),
            "held_back": len(self.held_back),
            "dropped": len(self.dropped),
            "redirect_sources": len(self.redirect_sources),
            "skipped_unpublished": len(self.skipped_unpublished),
        }


def _text(row: Row, name: str) -> str:
    value = row.get(name)
    return "" if value is None else str(value)


def _empty(value: object) -> bool:
    return value in (None, "", 0, "0")


class CatalogLoader:
    def __init__(
        self,
        bundle: Mapping[str, Any],
        *,
        ctx: AuditContext,
        include_unpublished: bool,
        attribute_aliases: Mapping[str, str],
    ) -> None:
        self.bundle = bundle
        self.ctx = ctx
        self.include_unpublished = include_unpublished
        self.aliases = {
            self.attribute_code(k): self.attribute_code(v) for k, v in attribute_aliases.items()
        }
        self.report = LoadReport()
        self.own = ChangeSet()  # rows this loader writes itself (services audit their own)
        self.languages = [str(code) for code, _name in settings.LANGUAGES]
        self.source = translations.source_language()
        self.vendors: dict[str, Vendor] = {}
        self.categories: dict[str, Category] = {}
        self.brands: dict[str, Brand] = {}
        self.terms: dict[str, Term] = {}
        self.attributes: dict[str, Attribute] = {}
        self.products: dict[str, Product] = {}  # by post id, any language
        self.legacy_languages: list[str] = []

    # -- helpers -------------------------------------------------------------------------

    @staticmethod
    def attribute_code(raw: object) -> str:
        text = str(raw or "").strip().lower().removeprefix("pa_")
        return slugify(text).replace("-", "_")

    def _attempt(self, key: str, action: Callable[..., Any], *args: Any) -> Any:
        """Run `action` in a savepoint; a rejected record is reported and the load goes on."""
        try:
            with transaction.atomic():
                return action(*args)
        except (ValidationError, IntegrityError, rules.BadValue, ValueError) as error:
            self.report.problems.append(f"{key}: {error}")
            return None

    def _created(self, model: str, key: str, after: Mapping[str, Any]) -> None:
        self.own.create(model, key, after)
        self.report.count(model)

    def _merge(self, model: str, changes: ChangeSet, *, new: bool = True) -> None:
        self.report.changes.changes.extend(changes.changes)
        if new:
            self.report.count(model)

    # -- entry point ---------------------------------------------------------------------

    def run(self) -> LoadReport:
        posts = [p for p in self.bundle.get("posts", []) if isinstance(p, Mapping)]
        loadable = self._classify(posts)
        # Only the languages the legacy shop had get a queue placeholder (EN and IT today).
        self.legacy_languages = [
            code for code in self.languages if any(p.get("lang") == code for p in posts)
        ]
        self._vendors()
        self._categories()
        self._brands()
        used_terms = {str(t) for p in loadable for t in p.get("tag_ids") or []}
        used_attributes = self._used_attributes(loadable)
        self._tags(used_terms)
        self._attributes(used_attributes)
        self._products(loadable)
        self.report.changes.changes.extend(self.own.changes)
        audit_changes(self.own, ctx=self.ctx, action=CREATED)
        return self.report

    # -- classification of the legacy posts ---------------------------------------------

    def _classify(self, posts: list[Row]) -> list[Row]:
        """The posts to load; trashed ones become redirect sources, unpublished ones depend on
        the vendor's MIG.16 choice."""
        loadable: list[Row] = []
        for post in posts:
            decision = rules.load_decision(
                post.get("status"), include_unpublished=self.include_unpublished
            )
            label = f"post {post.get('post_id')}"
            if decision == rules.SKIP_TRASH:
                self.report.redirect_sources.append(label)
            elif decision == rules.SKIP_UNPUBLISHED:
                self.report.skipped_unpublished.append(label)
            elif decision == rules.SKIP_UNKNOWN:
                self.report.problems.append(f"{label}: unknown status {post.get('status')!r}")
            else:
                loadable.append({**post, "_decision": decision})
        return loadable

    def _used_attributes(self, loadable: list[Row]) -> set[str]:
        used: set[str] = set()
        for post in loadable:
            for name in (*(post.get("attributes") or {}), *(post.get("options") or {})):
                code = self.attribute_code(name)
                used.add(self.aliases.get(code, code))
        return used

    # -- vendors -------------------------------------------------------------------------

    def _vendors(self) -> None:
        for row in self.bundle.get("vendors", []):
            user_id = str(row.get("user_id"))
            existing = Vendor.objects.filter(legacy_wp_user_id=row.get("user_id")).first()
            if existing is not None:
                self.vendors[user_id] = existing
                self.report.skipped_existing += 1
                continue
            vendor = self._attempt(f"vendor {user_id}", self._new_vendor, row)
            if vendor is not None:
                self.vendors[user_id] = vendor
                self._defer_vendor_data(row, vendor)

    def _new_vendor(self, row: Row) -> Vendor:
        # The status stays `applicant`: activation is a cutover step, never a side effect of the
        # load (the loaded vendor is not on the storefront until then).
        vendor = Vendor(
            legal_name=_text(row, "legal_name") or _text(row, "display_name"),
            display_name=_text(row, "display_name") or _text(row, "legal_name"),
            slug=_text(row, "slug"),
            country=_text(row, "country"),
            contact_email=_text(row, "email"),
            phone=_text(row, "phone"),
            legacy_wp_user_id=int(row["user_id"]),
        )
        self._merge("vendors.vendor", vendor_services.save_vendor(vendor, ctx=self.ctx))
        for language, texts in sorted(
            (row.get("translations") or {}).items(), key=lambda kv: kv[0] != self.source
        ):
            if language not in self.languages:
                self.report.problems.append(f"vendor {row['user_id']}: unknown language {language}")
                continue
            VendorTranslation.objects.create(
                vendor=vendor,
                language=language,
                status=PUBLISHED_STATUS,
                **{
                    name: _text(texts, name)
                    for name in VendorTranslation.translated_fields
                    if name in texts
                },
            )
        return vendor

    def _defer_vendor_data(self, row: Row, vendor: Vendor) -> None:
        """Hook: the vendor-level commission (a CommissionRule, VEN.2, loaded by MIG.4) and the
        VAT regime (VendorTaxProfile, `tax` app) are not loaded here."""
        if row.get("commission_percent") not in (None, ""):
            self.report.deferred.setdefault("commission", []).append(
                f"vendor {vendor.public_id}: {row['commission_percent']}"
            )
        if row.get("vat_regime") not in (None, ""):
            self.report.deferred.setdefault("vat_regime", []).append(
                f"vendor {vendor.public_id}: {row['vat_regime']}"
            )

    # -- categories, brands, tags --------------------------------------------------------

    def _categories(self) -> None:
        groups = rules.by_group(self.bundle.get("categories", []))
        group_of_term = {str(r.get("term_id")): key for key, rows in groups.items() for r in rows}
        pending = dict(groups)
        done: dict[str, Category] = {}
        while pending:
            progressed = False
            for key, rows in list(pending.items()):
                langs = rules.group_by_language(rows)
                english = langs.get(self.source)
                if english is None:
                    self.report.problems.append(f"category group {key}: no {self.source} record")
                    del pending[key]
                    continue
                parent: Category | None = None
                parent_term = english.get("parent_id")
                if not _empty(parent_term):
                    parent_group = group_of_term.get(str(parent_term))
                    if parent_group is None:
                        self.report.problems.append(
                            f"category group {key}: unknown parent {parent_term}"
                        )
                        del pending[key]
                        continue
                    if parent_group in pending:
                        continue
                    parent = done.get(parent_group)
                    if parent is None:
                        self.report.problems.append(f"category group {key}: parent not loaded")
                        del pending[key]
                        continue
                category = self._attempt(
                    f"category group {key}",
                    self._ensure_category,
                    langs,
                    parent,
                )
                del pending[key]
                progressed = True
                if category is not None:
                    done[key] = category
                    for row in rows:
                        self.categories[str(row.get("term_id"))] = category
            if not progressed:
                for key in pending:
                    self.report.problems.append(f"category group {key}: parent cycle")
                break

    def _ensure_category(self, langs: Mapping[str, Row], parent: Category | None) -> Category:
        english = langs[self.source]
        slug = _text(english, "slug") or slugify(_text(english, "name"))
        siblings = parent.get_children() if parent is not None else Category.get_root_nodes()
        existing = CategoryTranslation.objects.filter(
            category__in=siblings, language=self.source, slug=slug
        ).first()
        if existing is not None:
            self.report.skipped_existing += 1
            return existing.category
        category: Category = parent.add_child() if parent is not None else Category.add_root()
        for language in self._ordered(langs):
            row = langs[language]
            CategoryTranslation.objects.create(
                category=category,
                language=language,
                status=PUBLISHED_STATUS,
                name=_text(row, "name"),
                slug=_text(row, "slug") or slugify(_text(row, "name")),
                description=_text(row, "description"),
            )
        self._created("catalog.category", str(category.public_id), {"slug": slug})
        return category

    def _ordered(self, langs: Mapping[str, Any]) -> list[str]:
        """The source language first (it is the source of the others' hash), the known ones."""
        known = [code for code in langs if code in self.languages]
        for code in langs:
            if code not in self.languages:
                self.report.problems.append(f"unknown language {code!r} ignored")
        return sorted(known, key=lambda code: code != self.source)

    def _brands(self) -> None:
        for key, rows in rules.by_group(self.bundle.get("brands", [])).items():
            langs = rules.group_by_language(rows)
            english = langs.get(self.source)
            if english is None:
                self.report.problems.append(f"brand group {key}: no {self.source} record")
                continue
            brand = self._attempt(f"brand group {key}", self._ensure_brand, langs, english)
            if brand is not None:
                for row in rows:
                    self.brands[str(row.get("term_id"))] = brand

    def _ensure_brand(self, langs: Mapping[str, Row], english: Row) -> Brand:
        name = _text(english, "name")
        existing = Brand.objects.filter(name__iexact=name).first()
        if existing is not None:
            self.report.skipped_existing += 1
            return existing
        brand = Brand.objects.create(name=name)
        for language in self._ordered(langs):
            row = langs[language]
            BrandTranslation.objects.create(
                brand=brand,
                language=language,
                status=PUBLISHED_STATUS,
                slug=_text(row, "slug") or slugify(name),
                description=_text(row, "description"),
            )
        self._created("catalog.brand", str(brand.public_id), {"name": name})
        return brand

    def _tags(self, used_terms: set[str]) -> None:
        """Tags go to one taxonomy per `_legacy_tag_role`. A tag with no role waits for the
        MIG.2 decision list (reported, not loaded); a role no product uses is dropped."""
        by_role: dict[str, list[Row]] = {}
        for row in self.bundle.get("tags", []):
            role = slugify(_text(row, "role")).replace("-", "_")
            if not role:
                self.report.problems.append(
                    f"tag {row.get('term_id')}: no role, waits for the MIG.2 decision"
                )
                continue
            by_role.setdefault(role, []).append(row)
        for role, rows in sorted(by_role.items()):
            if not any(str(r.get("term_id")) in used_terms for r in rows):
                self.report.dropped.append(f"tag taxonomy {role}: no product uses it")
                continue
            taxonomy = self._attempt(f"taxonomy {role}", self._taxonomy, role)
            if taxonomy is None:
                continue
            for key, group in rules.by_group(rows).items():
                langs = rules.group_by_language(group)
                if self.source not in langs:
                    self.report.problems.append(f"tag group {key}: no {self.source} record")
                    continue
                term = self._attempt(
                    f"tag group {key}",
                    self._ensure_term,
                    taxonomy,
                    langs,
                )
                if term is not None:
                    for row in group:
                        self.terms[str(row.get("term_id"))] = term

    def _taxonomy(self, role: str) -> Taxonomy:
        taxonomy = Taxonomy.objects.filter(code=role).first()
        if taxonomy is not None:
            return taxonomy
        taxonomy = Taxonomy.objects.create(code=role, hierarchical=False, filterable=True)
        TaxonomyTranslation.objects.create(
            taxonomy=taxonomy,
            language=self.source,
            status=PUBLISHED_STATUS,
            name=role.replace("_", " ").title(),
            slug=role.replace("_", "-"),
        )
        self._created("catalog.taxonomy", str(taxonomy.public_id), {"code": role})
        return taxonomy

    def _ensure_term(self, taxonomy: Taxonomy, langs: Mapping[str, Row]) -> Term:
        english = langs[self.source]
        slug = _text(english, "slug") or slugify(_text(english, "name"))
        existing = TermTranslation.objects.filter(
            term__taxonomy=taxonomy, language=self.source, slug=slug
        ).first()
        if existing is not None:
            self.report.skipped_existing += 1
            return existing.term
        term: Term = Term.add_root(taxonomy=taxonomy)
        for language in self._ordered(langs):
            row = langs[language]
            TermTranslation.objects.create(
                term=term,
                language=language,
                status=PUBLISHED_STATUS,
                name=_text(row, "name"),
                slug=_text(row, "slug") or slugify(_text(row, "name")),
                description=_text(row, "description"),
            )
        self._created(
            "catalog.term", str(term.public_id), {"taxonomy": taxonomy.code, "slug": slug}
        )
        return term

    # -- typed attributes ----------------------------------------------------------------

    def _attributes(self, used: set[str]) -> None:
        """Typed attributes. Typo and duplicate names merge through `attribute_aliases`; an
        attribute no loaded product uses is dropped."""
        merged: dict[str, dict[str, Any]] = {}
        for row in self.bundle.get("attributes", []):
            code = self.attribute_code(row.get("code"))
            code = self.aliases.get(code, code)
            entry = merged.setdefault(code, {"row": row, "values": []})
            entry["values"].extend(row.get("values") or [])
        for position, (code, entry) in enumerate(sorted(merged.items())):
            if code not in used:
                self.report.dropped.append(f"attribute {code}: no product uses it")
                continue
            attribute = self._attempt(
                f"attribute {code}",
                self._ensure_attribute,
                code,
                entry,
                position,
            )
            if attribute is not None:
                self.attributes[code] = attribute

    def _ensure_attribute(self, code: str, entry: Mapping[str, Any], position: int) -> Attribute:
        row = entry["row"]
        attribute = Attribute.objects.filter(code=code).first()
        if attribute is None:
            kind = _text(row, "kind") or AttributeKind.CHOICE
            attribute = Attribute(
                code=code,
                kind=kind,
                unit=_text(row, "unit") if kind == AttributeKind.NUMBER else "",
                position=position,
            )
            attribute.full_clean()
            attribute.save()
            labels = row.get("labels") or {self.source: code.replace("_", " ").title()}
            for language in self._ordered(labels):
                AttributeTranslation.objects.create(
                    attribute=attribute,
                    language=language,
                    status=PUBLISHED_STATUS,
                    name=str(labels[language]),
                )
            self._created("catalog.attribute", str(attribute.public_id), {"code": code})
        else:
            self.report.skipped_existing += 1
        if attribute.kind == AttributeKind.CHOICE:
            for group in rules.by_group(entry["values"]).values():
                langs = rules.group_by_language(group)
                english = langs.get(self.source) or next(iter(langs.values()))
                self._ensure_value(
                    attribute,
                    _text(english, "value") or _text(english, "label"),
                    {code_: _text(r, "label") or _text(r, "value") for code_, r in langs.items()},
                )
        return attribute

    def _ensure_value(
        self, attribute: Attribute, raw: str, labels: Mapping[str, str] | None = None
    ) -> AttributeValue:
        value = slugify(raw)
        found = AttributeValue.objects.filter(attribute=attribute, value=value).first()
        if found is not None:
            return found
        created = AttributeValue(
            attribute=attribute, value=value, position=attribute.values.count()
        )
        created.full_clean()
        created.save()
        texts = dict(labels or {self.source: raw})
        for language in self._ordered(texts):
            AttributeValueTranslation.objects.create(
                value=created, language=language, status=PUBLISHED_STATUS, label=texts[language]
            )
        self._created("catalog.attributevalue", str(created.public_id), {"value": value})
        return created

    # -- products and variants -----------------------------------------------------------

    def _products(self, loadable: list[Row]) -> None:
        product_posts = [p for p in loadable if p.get("post_type", rules.PRODUCT) == rules.PRODUCT]
        variation_posts = [p for p in loadable if p.get("post_type") == rules.VARIATION]
        seen_sku: dict[tuple[str, str], str] = {}
        grouped: dict[tuple[str, str, str], list[Row]] = {}
        for post in product_posts:
            sku = _text(post, "sku")
            key = rules.group_key(
                post.get("author_id"), catalog_engine.sku_match_key(sku), post.get("group_id")
            )
            grouped.setdefault(key, []).append(post)
        accepted: list[tuple[tuple[str, str, str], dict[str, Row]]] = []
        for key, rows in grouped.items():
            langs = rules.group_by_language(rows)
            if self.source not in langs:
                self.report.problems.append(f"product {key[1]!r}: no {self.source} post")
                continue
            owner = seen_sku.setdefault((key[0], key[1]), key[2])
            if owner != key[2]:
                self.report.problems.append(
                    f"product {key[1]!r} of vendor {key[0]}: the SKU is also used by translation "
                    f"group {owner}; not merged (MIG.2 decision)"
                )
                continue
            accepted.append((key, langs))
        post_to_group = {
            str(post.get("post_id")): key for key, langs in accepted for post in langs.values()
        }
        variations: dict[tuple[str, str, str], list[Row]] = {}
        for post in variation_posts:
            parent = post_to_group.get(str(post.get("parent_id")))
            if parent is None:
                self.report.problems.append(
                    f"variation post {post.get('post_id')}: its product is not loaded"
                )
                continue
            variations.setdefault(parent, []).append(post)
        for key, langs in accepted:
            product = self._attempt(
                f"product {key[1]!r}",
                self._ensure_product,
                key,
                langs,
                variations.get(key, []),
            )
            if product is not None:
                for post in langs.values():
                    self.products[str(post.get("post_id"))] = product
        self._relations(accepted)

    def _ensure_product(
        self, key: tuple[str, str, str], langs: Mapping[str, Row], variation_posts: list[Row]
    ) -> Product | None:
        english = langs[self.source]
        vendor = self._vendor_of(english)
        sku_key = key[1]
        existing = Product.objects.filter(vendor=vendor, sku_key=sku_key).first()
        if existing is not None:
            legacy_id = english.get("post_id")
            if existing.legacy_wp_id_en not in (None, legacy_id):
                raise ValidationError(
                    f"the SKU belongs to another legacy product (post {existing.legacy_wp_id_en})"
                )
            self.report.skipped_existing += 1
            self._ensure_variants(existing, variation_posts)
            return existing
        variants = rules.by_group(variation_posts)
        prices = [
            p
            for p in (
                rules.parse_money(
                    rules.group_by_language(v).get(self.source, v[0]).get("price_net"),
                    field="variation price",
                )
                for v in variants.values()
            )
            if p is not None
        ]
        price = rules.parse_money(english.get("price_net"), field="price")
        if price == 0:
            price = None
        if price is None and prices:
            price = min(prices)
        sale = rules.parse_money(english.get("sale_price_net"), field="sale price")
        if sale is not None and (price is None or not 0 < sale < price):
            self.report.problems.append(
                f"product {key[1]!r}: sale price dropped (not below the price)"
            )
            sale = None
        status_backorder = (
            rules.stock_status(english.get("stock_status")) == StockStatus.ON_BACKORDER
        )
        product = Product(
            vendor=vendor,
            sku=_text(english, "sku").strip(),
            kind=ProductKind.VARIABLE
            if variants or english.get("type") == "variable"
            else ProductKind.SIMPLE,
            sales_mode=SalesMode.BUY if price is not None else SalesMode.CATALOG_ONLY,
            price_net=price,
            sale_price_net=sale,
            sale_from=self._when(english.get("sale_from")) if sale is not None else None,
            sale_to=self._when(english.get("sale_to")) if sale is not None else None,
            stock_qty=rules.parse_quantity(english.get("stock_qty")),
            stock_status=rules.stock_status(english.get("stock_status")),
            stock_policy=StockPolicy.ALWAYS_OPEN if status_backorder else StockPolicy.TRACK,
            weight_kg=rules.parse_decimal(english.get("weight_kg"), field="weight"),
            gtin=self._gtin(english, key[1]),
            mpn=_text(english, "mpn")[:70],
            legacy_wp_id_en=self._legacy_id(english),
            legacy_wp_id_it=self._legacy_id(langs.get("it")),
            status=ProductStatus.DRAFT,
        )
        category_ids = [str(c) for c in english.get("category_ids") or []]
        product.category = next(
            (self.categories[c] for c in category_ids if c in self.categories), None
        )
        product.brand = self.brands.get(str(english.get("brand_id")))
        self._merge("catalog.product", catalog_services.save_product(product, ctx=self.ctx))
        product.secondary_categories.set(
            {self.categories[c] for c in category_ids if c in self.categories} - {product.category}
        )
        product.terms.set(
            {self.terms[t] for t in map(str, english.get("tag_ids") or []) if t in self.terms}
        )
        self._texts(product, langs, product.sku)
        self._product_attributes(product, english, variants)
        self._ensure_variants(product, variation_posts)
        self._publish_if_live(product, english)
        self._defer_product_commission(product, english)
        return product

    def _vendor_of(self, post: Row) -> Vendor:
        author = str(post.get("author_id"))
        vendor = (
            self.vendors.get(author)
            or Vendor.objects.filter(legacy_wp_user_id=post.get("author_id")).first()
        )
        if vendor is None:
            raise ValidationError(f"vendor {author} is not loaded")
        return vendor

    @staticmethod
    def _legacy_id(post: Row | None) -> int | None:
        if post is None or _empty(post.get("post_id")):
            return None
        return int(post["post_id"])

    def _gtin(self, post: Row, sku: str) -> str:
        gtin = _text(post, "gtin").strip()
        if gtin and not (gtin.isdigit() and 8 <= len(gtin) <= 14):
            self.report.problems.append(
                f"product {sku!r}: GTIN {gtin!r} dropped (not 8 to 14 digits)"
            )
            return ""
        return gtin

    @staticmethod
    def _when(raw: object) -> dt.datetime | None:
        if _empty(raw):
            return None
        try:
            value = dt.datetime.fromisoformat(str(raw))
        except ValueError:
            raise rules.BadValue(f"{raw!r} is not a date") from None
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)

    def _texts(self, product: Product, langs: Mapping[str, Row], sku: str) -> None:
        """The legacy texts as published translations (D-18); a language with no legacy twin
        gets a `machine` placeholder, which is its translation-queue entry."""
        for language in self._ordered(langs):
            row = langs[language]
            text = ProductTranslation.objects.create(
                product=product,
                language=language,
                status=PUBLISHED_STATUS,
                title=_text(row, "title"),
                short_description=_text(row, "short_description"),
                description=_text(row, "description"),
                seo_title=_text(row, "seo_title"),
                seo_description=_text(row, "seo_description"),
            )
            catalog_services.sync_product_slug(text, ctx=self.ctx)
        english = langs[self.source]
        for language in rules.missing_languages(langs, self.legacy_languages, self.source):
            text = ProductTranslation.objects.create(
                product=product,
                language=language,
                status=MACHINE_STATUS,
                title=_text(english, "title"),
                short_description=_text(english, "short_description"),
                description=_text(english, "description"),
            )
            catalog_services.sync_product_slug(text, ctx=self.ctx)
            self.report.translation_queue.append(f"product {sku!r} [{language}]")

    def _product_attributes(
        self, product: Product, english: Row, variants: Mapping[str, list[Row]]
    ) -> None:
        axes: dict[str, set[str]] = {}
        for group in variants.values():
            post = rules.group_by_language(group).get(self.source, group[0])
            for name, value in (post.get("options") or {}).items():
                code = self.attribute_code(name)
                axes.setdefault(self.aliases.get(code, code), set()).add(str(value))
        values: dict[str, Any] = {}
        for name, value in (english.get("attributes") or {}).items():
            code = self.attribute_code(name)
            values[self.aliases.get(code, code)] = value
        for position, code in enumerate([*axes, *(c for c in values if c not in axes)]):
            attribute = self.attributes.get(code)
            if attribute is None:
                self.report.problems.append(
                    f"product {product.sku!r}: attribute {code!r} not loaded"
                )
                continue
            raw = values.get(code)
            entry = ProductAttribute(product=product, attribute=attribute, position=position)
            if attribute.kind == AttributeKind.CHOICE:
                entry.is_variant_axis = code in axes
                entry.save()
                chosen = {
                    *(axes.get(code) or set()),
                    *(raw if isinstance(raw, list) else [raw] if raw not in (None, "") else []),
                }
                entry.values.set([self._ensure_value(attribute, str(v)) for v in sorted(chosen)])
            elif attribute.kind == AttributeKind.NUMBER:
                entry.numeric_value = rules.parse_decimal(raw, field=code)
                entry.save()
            else:
                entry.text_value = _text({"v": raw}, "v")[:255]
                entry.save()

    def _ensure_variants(self, product: Product, variation_posts: list[Row]) -> None:
        for position, (key, group) in enumerate(rules.by_group(variation_posts).items()):
            langs = rules.group_by_language(group)
            english = langs.get(self.source)
            if english is None:
                self.report.problems.append(f"variation group {key}: no {self.source} post")
                continue
            self._attempt(
                f"variation {_text(english, 'sku')!r}",
                self._ensure_variant,
                product,
                langs,
                english,
                position,
            )

    def _ensure_variant(
        self, product: Product, langs: Mapping[str, Row], english: Row, position: int
    ) -> None:
        sku = _text(english, "sku").strip()
        if not sku:
            raise ValidationError("a variation needs a SKU")
        if Variant.objects.filter(
            vendor=product.vendor, sku_key=catalog_engine.sku_match_key(sku)
        ).exists():
            self.report.skipped_existing += 1
            return
        status = rules.stock_status(english.get("stock_status"))
        variant = Variant(
            product=product,
            sku=sku,
            price_net_override=rules.parse_money(english.get("price_net"), field="variation price"),
            weight_kg_override=rules.parse_decimal(english.get("weight_kg"), field="weight"),
            stock_qty=rules.parse_quantity(english.get("stock_qty")),
            stock_status=status,
            is_default=position == 0,
            position=position,
        )
        if variant.price_net_override == 0:
            variant.price_net_override = None
        self._merge("catalog.variant", catalog_services.save_variant(variant, ctx=self.ctx))
        for name, value in (english.get("options") or {}).items():
            code = self.attribute_code(name)
            attribute = self.attributes.get(self.aliases.get(code, code))
            if attribute is None or attribute.kind != AttributeKind.CHOICE:
                self.report.problems.append(f"variation {sku!r}: option {code!r} not loaded")
                continue
            VariantOption.objects.create(
                variant=variant,
                attribute=attribute,
                value=self._ensure_value(attribute, str(value)),
            )
        for language in self._ordered(langs):
            row = langs[language]
            VariantTranslation.objects.create(
                variant=variant,
                language=language,
                status=PUBLISHED_STATUS,
                label=_text(row, "title") or sku,
                description=_text(row, "description"),
            )
        base = langs[self.source]
        for language in rules.missing_languages(langs, self.legacy_languages, self.source):
            VariantTranslation.objects.create(
                variant=variant,
                language=language,
                status=MACHINE_STATUS,
                label=_text(base, "title") or sku,
                description=_text(base, "description"),
            )
            self.report.translation_queue.append(f"variant {sku!r} [{language}]")

    def _publish_if_live(self, product: Product, english: Row) -> None:
        """Live in the legacy shop and publishable here: published. Otherwise it stays a hidden
        draft; a live product blocked by a publication check is reported as held back."""
        if english.get("_decision") != rules.LOAD_PUBLISHED:
            return
        blockers = publishing.publication_blockers(product)
        if blockers:
            self.report.held_back.append(f"product {product.sku!r}: {', '.join(sorted(blockers))}")
            return
        product.status = ProductStatus.PUBLISHED
        changes = catalog_services.save_product(product, ctx=self.ctx)
        self._merge("catalog.product", changes, new=False)  # the same product, not a second one

    def _defer_product_commission(self, product: Product, english: Row) -> None:
        """Hook: a per-product commission override becomes a product-level CommissionRule
        (VEN.2, VEN.33) only after the owner approves the list; CommissionRule is not on this
        base, so the override is listed for MIG.4."""
        if english.get("commission_override_percent") not in (None, ""):
            self.report.deferred.setdefault("commission", []).append(
                f"product {product.public_id}: {english['commission_override_percent']}"
            )

    def _relations(self, accepted: list[tuple[tuple[str, str, str], dict[str, Row]]]) -> None:
        """Required companions and upsells by SKU, once every product exists."""
        for key, langs in accepted:
            english = langs[self.source]
            product = self.products.get(str(english.get("post_id")))
            companion_sku = _text(english, "companion_sku").strip()
            upsell_skus = [str(s) for s in english.get("upsell_skus") or []]
            if product is None or not (companion_sku or upsell_skus):
                continue

            def link(
                product: Product = product,
                companion_sku: str = companion_sku,
                upsell_skus: list[str] = upsell_skus,
            ) -> None:
                before = catalog_services.relations_snapshot(product)
                found = {
                    sku: Product.objects.filter(
                        vendor=product.vendor, sku_key=catalog_engine.sku_match_key(sku)
                    ).first()
                    for sku in [companion_sku, *upsell_skus]
                    if sku
                }
                for sku, other in found.items():
                    if other is None:
                        self.report.problems.append(
                            f"product {product.sku!r}: related SKU {sku!r} not found"
                        )
                companion = found.get(companion_sku)
                if companion is not None:
                    product.required_companion = companion
                    product.save(update_fields=["required_companion", "updated_at"])
                product.upsells.add(
                    *[p for s, p in found.items() if p is not None and s != companion_sku]
                )
                catalog_services.record_relations_change(product, before, ctx=self.ctx)

            self._attempt(f"relations of {key[1]!r}", link)
