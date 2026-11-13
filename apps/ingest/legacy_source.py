"""Read-only legacy source connector (MIG.1, part 1). It reads a legacy-shaped database through a
DB-API connection and never writes. Table names carry a neutral configurable prefix
(`MPMV_LEGACY_DB_PREFIX`, fake default `legacy_`); the real prefix is never versioned.

Part 1 is built and tested against a fixture schema only (SQLite) that mirrors the public data
map of spec 13 §13.2. Queries use `?` placeholders (the fixture driver); a MySQL transport maps
them in part 2. No Django model is imported."""

import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from django.conf import settings

from apps.ingest import legacy_engine as rules

DEFAULT_PREFIX: Final = "legacy_"
_PREFIX_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,30}_")
_ALLOWED_VERBS: Final = ("select",)


class ReadOnlyViolation(RuntimeError):
    """A statement other than a SELECT reached the legacy source."""


def legacy_prefix() -> str:
    prefix = str(getattr(settings, "MPMV_LEGACY_DB_PREFIX", "") or DEFAULT_PREFIX)
    if not _PREFIX_RE.fullmatch(prefix):
        raise ValueError("MPMV_LEGACY_DB_PREFIX must be lowercase letters, digits and underscores.")
    return prefix


def _deny_writes(action: int, *_args: Any) -> int:
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


@dataclass(frozen=True)
class RedirectCodeMaps:
    """The code-side maps of the legacy system (no table holds them): old path to new target,
    supplied by the caller from the private inventory."""

    retired_skus: Mapping[str, str] = field(default_factory=dict)
    merged_tags_en: Mapping[str, str] = field(default_factory=dict)
    merged_tags_it: Mapping[str, str] = field(default_factory=dict)
    old_categories: Mapping[str, str] = field(default_factory=dict)
    old_brand_base: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LegacyProduct:
    post_id: int
    post_type: str  # product | product_variation
    status: str
    key: rules.ProductKey
    language: str
    parent_id: int


@dataclass(frozen=True)
class RedirectSource:
    kind: rules.RedirectSourceKind
    old: str
    target: str  # post id as text for slug sources, new path for the code maps
    language: str = ""


@dataclass(frozen=True)
class LegacyCustomer:
    user_id: int
    last_order_at: datetime | None
    last_active_at: datetime | None


@dataclass(frozen=True)
class LegacyOrder:
    order_id: int
    status: str
    created_at: datetime
    total: Decimal
    meta: Mapping[str, str]


@dataclass
class ProductReport:
    products: list[LegacyProduct] = field(default_factory=list)
    skipped_no_sku: list[int] = field(default_factory=list)
    skipped_no_group: list[int] = field(default_factory=list)
    collisions: list[tuple[rules.ProductKey, str, list[int]]] = field(default_factory=list)
    vendor_mismatches: list[int] = field(default_factory=list)
    language_mismatches: list[int] = field(default_factory=list)
    untranslated: list[rules.ProductKey] = field(default_factory=list)


@dataclass
class OrderReport:
    orders: list[LegacyOrder] = field(default_factory=list)
    drift: list[rules.DriftFinding] = field(default_factory=list)
    malformed: list[tuple[int, str]] = field(default_factory=list)


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=UTC) if value.isdigit() else _dt(value)


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class LegacyReader:
    """Read-only access to one legacy database. Every statement must be a SELECT."""

    def __init__(self, connection: sqlite3.Connection, prefix: str | None = None) -> None:
        self.prefix = prefix if prefix is not None else legacy_prefix()
        if not _PREFIX_RE.fullmatch(self.prefix):
            raise ValueError("Invalid legacy table prefix.")
        connection.set_authorizer(_deny_writes)
        self._conn = connection

    @classmethod
    def open_sqlite(cls, path: Path, prefix: str | None = None) -> "LegacyReader":
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        return cls(conn, prefix)

    def close(self) -> None:
        self._conn.close()

    def t(self, name: str) -> str:
        return f"{self.prefix}{name}"

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
        if (
            ";" in sql.strip().rstrip(";")
            or sql.lstrip().split(None, 1)[0].lower() not in _ALLOWED_VERBS
        ):
            raise ReadOnlyViolation("The legacy source is read-only: only SELECT is allowed.")
        try:
            return self._conn.execute(sql, tuple(params)).fetchall()
        except sqlite3.DatabaseError as exc:
            if "not authorized" in str(exc):
                raise ReadOnlyViolation(str(exc)) from exc
            raise

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Present only to refuse: the connector never writes."""
        raise ReadOnlyViolation("The legacy source is read-only.")

    def _meta(
        self, table: str, id_col: str, ids: Sequence[int], keys: Sequence[str]
    ) -> dict[int, dict[str, str]]:
        out: dict[int, dict[str, str]] = {i: {} for i in ids}
        if not ids or not keys:
            return out
        marks = ",".join("?" * len(keys))
        for owner, key, value in self.query(
            f"SELECT {id_col}, meta_key, meta_value FROM {self.t(table)} "  # noqa: S608 - prefix validated
            f"WHERE meta_key IN ({marks})",
            list(keys),
        ):
            if owner in out:
                out[owner][key] = value
        return out

    # --- Products

    def products(self) -> ProductReport:
        """Products and variations keyed by vendor + SKU match key + translation group.
        Trashed posts are not products (see `redirect_sources`)."""
        report = ProductReport()
        rows = self.query(
            f"SELECT p.id, p.post_type, p.post_status, p.post_author, p.post_parent, "  # noqa: S608
            f"tr.trid, tr.language_code FROM {self.t('posts')} p "
            f"LEFT JOIN {self.t('translations')} tr ON tr.element_id = p.id "
            "AND tr.element_type = 'post_' || p.post_type "
            "WHERE p.post_type IN ('product', 'product_variation') AND p.post_status <> 'trash' "
            "ORDER BY p.id"
        )
        ids = [r[0] for r in rows]
        meta = self._meta("postmeta", "post_id", ids, ["_sku", "_legacy_lang", "_vendor_id"])
        keyed: list[rules.KeyedPost] = []
        for pid, ptype, status, author, parent, trid, lang in rows:
            if trid is None or not lang:
                report.skipped_no_group.append(pid)
                continue
            try:
                key = rules.product_key(author, meta[pid].get("_sku", ""), trid)
            except rules.LegacyDataError:
                report.skipped_no_sku.append(pid)
                continue
            if "_vendor_id" in meta[pid] and meta[pid]["_vendor_id"] != str(author):
                report.vendor_mismatches.append(pid)
            if meta[pid].get("_legacy_lang", lang) != lang:
                report.language_mismatches.append(pid)
            report.products.append(LegacyProduct(pid, ptype, status, key, lang, parent))
            keyed.append(rules.KeyedPost(pid, key, lang))
        report.collisions = rules.find_key_collisions(keyed)
        langs: dict[tuple[str, rules.ProductKey], set[str]] = {}
        for prod in report.products:
            langs.setdefault((prod.post_type, prod.key), set()).add(prod.language)
        report.untranslated = sorted({k for (_t, k), v in langs.items() if len(v) < 2})
        return report

    # --- Redirect sources

    def redirect_sources(self, code_maps: RedirectCodeMaps | None = None) -> list[RedirectSource]:
        """The seven redirect sources of the spec (two post metas plus five code maps) and the
        slugs of trashed products (trash suffix stripped)."""
        maps = code_maps or RedirectCodeMaps()
        out: list[RedirectSource] = []
        posts = self.query(
            f"SELECT p.id, p.post_status, p.post_name, COALESCE(tr.language_code, '') "  # noqa: S608
            f"FROM {self.t('posts')} p LEFT JOIN {self.t('translations')} tr "
            "ON tr.element_id = p.id AND tr.element_type = 'post_' || p.post_type "
            "WHERE p.post_type = 'product' ORDER BY p.id"
        )
        meta = self._meta(
            "postmeta", "post_id", [r[0] for r in posts], ["_legacy_old_slugs", "_wp_old_slug"]
        )
        for pid, status, name, lang in posts:
            if status == "trash":
                out.append(
                    RedirectSource(
                        rules.RedirectSourceKind.TRASHED_PRODUCT,
                        rules.strip_trash_suffix(name),
                        str(pid),
                        lang,
                    )
                )
                continue
            for slug in rules.split_old_slugs(meta[pid].get("_legacy_old_slugs", ""))[
                : rules.CUSTOM_OLD_SLUGS_CAP
            ]:
                out.append(
                    RedirectSource(rules.RedirectSourceKind.CUSTOM_OLD_SLUGS, slug, str(pid), lang)
                )
            core = meta[pid].get("_wp_old_slug", "").strip()
            if core:
                out.append(
                    RedirectSource(rules.RedirectSourceKind.CORE_OLD_SLUG, core, str(pid), lang)
                )
        kinds = rules.RedirectSourceKind
        for kind, mapping in (
            (kinds.RETIRED_SKU_MAP, maps.retired_skus),
            (kinds.MERGED_TAG_MAP_EN, maps.merged_tags_en),
            (kinds.MERGED_TAG_MAP_IT, maps.merged_tags_it),
            (kinds.OLD_CATEGORY_MAP, maps.old_categories),
            (kinds.OLD_BRAND_BASE, maps.old_brand_base),
        ):
            out.extend(RedirectSource(kind, old, new) for old, new in sorted(mapping.items()))
        return out

    # --- Orders (posts store is authoritative)

    def _posts_orders(self) -> dict[int, tuple[str, str]]:
        return {
            r[0]: (r[1], r[2])
            for r in self.query(
                f"SELECT id, post_status, post_date FROM {self.t('posts')} "  # noqa: S608
                "WHERE post_type = 'shop_order' ORDER BY id"
            )
        }

    def orders(self) -> OrderReport:
        """Orders from the posts store only, after a drift check against the other store.
        Only the tax-archive meta keys are read."""
        report = OrderReport()
        posts = self._posts_orders()
        ids = list(posts)
        rules.assert_tax_archive_keys(rules.TAX_ARCHIVE_ORDER_META)
        meta = self._meta("postmeta", "post_id", ids, rules.TAX_ARCHIVE_ORDER_META)
        snapshots: dict[int, rules.OrderSnapshot] = {}
        for oid in ids:
            status, created = posts[oid]
            try:
                total = rules.parse_money(meta[oid].get("_order_total"))
                for key in (
                    "_order_tax",
                    "_order_shipping",
                    "_order_shipping_tax",
                    "_cart_discount",
                ):
                    if key in meta[oid]:
                        rules.parse_money(meta[oid][key])
            except rules.LegacyDataError as exc:
                report.malformed.append((oid, str(exc)))
                continue
            snapshots[oid] = rules.OrderSnapshot(oid, status, total)
            report.orders.append(LegacyOrder(oid, status, _dt(created), total, meta[oid]))
        secondary: dict[int, rules.OrderSnapshot] = {}
        for oid, status, total_raw in self.query(
            f"SELECT id, status, total_amount FROM {self.t('orders_secondary')} ORDER BY id"  # noqa: S608
        ):
            try:
                secondary[oid] = rules.OrderSnapshot(oid, status, rules.parse_money(total_raw))
            except rules.LegacyDataError as exc:
                if oid not in {m[0] for m in report.malformed}:
                    report.malformed.append((oid, f"secondary store: {exc}"))
        report.drift = rules.compare_order_stores(snapshots, secondary)
        self._guard_items(ids)
        return report

    def _guard_items(self, order_ids: Sequence[int]) -> None:
        wanted = set(order_ids)
        for oid, item_type, name in self.query(
            f"SELECT order_id, order_item_type, order_item_name FROM {self.t('order_items')}"  # noqa: S608
        ):
            if oid in wanted:
                rules.guard_vat_like_fee(item_type, name)

    # --- Customers

    def customers(
        self, *, now: datetime, activity_window_months: int = rules.DEFAULT_ACTIVITY_WINDOW_MONTHS
    ) -> list[LegacyCustomer]:
        """Active customers: role filter first, then the activity rule. The window is the
        vendor's MIG.16 choice, passed in."""
        users = [r[0] for r in self.query(f"SELECT id FROM {self.t('users')} ORDER BY id")]  # noqa: S608
        meta = self._meta("usermeta", "user_id", users, ["capabilities", "last_active"])
        last_order: dict[int, datetime] = {}
        orders = self._posts_orders()
        owners = self._meta("postmeta", "post_id", list(orders), ["_customer_user"])
        for oid, (_status, created) in orders.items():
            owner = owners[oid].get("_customer_user", "")
            if owner.isdigit():
                at = _dt(created)
                last_order[int(owner)] = max(at, last_order.get(int(owner), at))
        out = []
        for uid in users:
            last_active = _ts(meta[uid].get("last_active"))
            if rules.is_active_customer(
                rules.roles_from_meta(meta[uid].get("capabilities", "")),
                last_order_at=last_order.get(uid),
                last_active_at=last_active,
                now=now,
                activity_window_months=activity_window_months,
            ):
                out.append(LegacyCustomer(uid, last_order.get(uid), last_active))
        return out
