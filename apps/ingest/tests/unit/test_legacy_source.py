# ruff: noqa: S608
"""MIG.1 part 1: the legacy extractor rules, against a fixture schema only (SQLite built here).
No real legacy table prefix appears anywhere: the fixture uses the fake default."""

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.test import override_settings

from apps.ingest import legacy_engine as rules
from apps.ingest.legacy_source import (
    DEFAULT_PREFIX,
    LegacyReader,
    ReadOnlyViolation,
    RedirectCodeMaps,
    legacy_prefix,
)

P = DEFAULT_PREFIX
NOW = datetime(2026, 11, 12, tzinfo=UTC)

SCHEMA = f"""
CREATE TABLE {P}posts (id INTEGER PRIMARY KEY, post_author INT, post_type TEXT, post_status TEXT,
  post_name TEXT, post_parent INT DEFAULT 0, post_date TEXT);
CREATE TABLE {P}postmeta (post_id INT, meta_key TEXT, meta_value TEXT);
CREATE TABLE {P}users (id INTEGER PRIMARY KEY);
CREATE TABLE {P}usermeta (user_id INT, meta_key TEXT, meta_value TEXT);
CREATE TABLE {P}translations (element_id INT, element_type TEXT, trid INT, language_code TEXT);
CREATE TABLE {P}order_items (order_item_id INTEGER PRIMARY KEY, order_id INT,
  order_item_type TEXT, order_item_name TEXT);
CREATE TABLE {P}orders_secondary (id INTEGER PRIMARY KEY, status TEXT, total_amount TEXT);
"""


class Db:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.c = sqlite3.connect(path)
        self.c.executescript(SCHEMA)

    def post(
        self,
        pid: int,
        ptype: str,
        *,
        author: int = 1,
        status: str = "publish",
        name: str = "x",
        date: str = "2026-01-01",
        parent: int = 0,
    ) -> None:
        self.c.execute(
            f"INSERT INTO {P}posts VALUES (?,?,?,?,?,?,?)",
            (pid, author, ptype, status, name, parent, date),
        )

    def meta(self, pid: int, **kv: str) -> None:
        for k, v in kv.items():
            self.c.execute(f"INSERT INTO {P}postmeta VALUES (?,?,?)", (pid, k, v))

    def tr(self, pid: int, ptype: str, trid: int, lang: str) -> None:
        self.c.execute(
            f"INSERT INTO {P}translations VALUES (?,?,?,?)", (pid, f"post_{ptype}", trid, lang)
        )

    def user(self, uid: int, role: str, last_active: str | None = None) -> None:
        self.c.execute(f"INSERT INTO {P}users VALUES (?)", (uid,))
        self.c.execute(f"INSERT INTO {P}usermeta VALUES (?,?,?)", (uid, "capabilities", role))
        if last_active:
            self.c.execute(
                f"INSERT INTO {P}usermeta VALUES (?,?,?)", (uid, "last_active", last_active)
            )

    def order(
        self,
        oid: int,
        *,
        total: str = "10.00",
        status: str = "completed",
        date: str = "2026-06-01",
        user: int | None = None,
        secondary: bool = True,
        extra: dict[str, str] | None = None,
    ) -> None:
        self.post(oid, "shop_order", status=status, date=date)
        self.meta(oid, _order_total=total, **(extra or {}))
        if user:
            self.meta(oid, _customer_user=str(user))
        if secondary:
            self.c.execute(f"INSERT INTO {P}orders_secondary VALUES (?,?,?)", (oid, status, total))

    def reader(self) -> LegacyReader:
        self.c.commit()
        return LegacyReader.open_sqlite(self.path, P)


@pytest.fixture
def db(tmp_path: Path) -> Db:
    return Db(tmp_path / "legacy.sqlite")


# --- Prefix


def test_default_prefix_is_neutral() -> None:
    with override_settings(MPMV_LEGACY_DB_PREFIX=""):
        assert legacy_prefix() == "legacy_"


def test_prefix_is_validated() -> None:
    with (
        override_settings(MPMV_LEGACY_DB_PREFIX="x; DROP TABLE y;--"),
        pytest.raises(ValueError, match="MPMV_LEGACY"),
    ):
        legacy_prefix()


# --- Read-only


def test_connector_refuses_writes(db: Db) -> None:
    reader = db.reader()
    with pytest.raises(ReadOnlyViolation):
        reader.execute(f"INSERT INTO {P}users VALUES (9)")
    with pytest.raises(ReadOnlyViolation):
        reader.query(f"DELETE FROM {P}users")
    with pytest.raises(ReadOnlyViolation):
        reader.query(f"SELECT 1; DROP TABLE {P}users")
    assert reader.query(f"SELECT COUNT(*) FROM {P}users") == [(0,)]


def test_connection_denies_writes_even_if_the_guard_were_bypassed(db: Db) -> None:
    reader = db.reader()
    with pytest.raises(sqlite3.DatabaseError):
        reader._conn.execute(f"INSERT INTO {P}users VALUES (9)")


# --- Products: vendor + SKU match key + translation group


def _twins(
    db: Db,
    base: int,
    trid: int,
    sku: str,
    *,
    author: int = 1,
    langs: tuple[str, ...] = ("en", "it"),
) -> None:
    for i, lang in enumerate(langs):
        db.post(base + i, "product", author=author, name=f"p{base + i}")
        db.meta(base + i, _sku=sku)
        db.tr(base + i, "product", trid, lang)


def test_twins_share_vendor_sku_and_group(db: Db) -> None:
    _twins(db, 10, 100, " Ab-1 ")
    report = db.reader().products()
    keys = {p.key for p in report.products}
    assert len(keys) == 1
    assert {p.language for p in report.products} == {"en", "it"}
    assert report.collisions == []
    assert report.untranslated == []


def test_same_sku_different_vendor_or_group_is_a_different_key(db: Db) -> None:
    _twins(db, 10, 100, "AB-1", author=1)
    _twins(db, 20, 200, "AB-1", author=2)
    _twins(db, 30, 300, "AB-1", author=1)
    report = db.reader().products()
    assert len({p.key for p in report.products}) == 3
    assert report.collisions == []


def test_untranslated_product_and_variation_are_reported(db: Db) -> None:
    _twins(db, 10, 100, "A", langs=("en",))
    db.post(11, "product_variation", parent=10)
    db.meta(11, _sku="A-V")
    db.tr(11, "product_variation", 111, "en")
    db.post(12, "product_variation", parent=10)
    db.meta(12, _sku="A-W")
    db.tr(12, "product_variation", 112, "en")
    db.post(13, "product_variation", parent=10)
    db.meta(13, _sku="A-W")
    db.tr(13, "product_variation", 112, "it")
    report = db.reader().products()
    assert len(report.untranslated) == 2  # the product and one variation, not the twinned one


def test_collision_in_same_group_and_language(db: Db) -> None:
    _twins(db, 10, 100, "A", langs=("en",))
    _twins(db, 20, 100, "A", langs=("en",))
    report = db.reader().products()
    assert [ids for _k, _l, ids in report.collisions] == [[10, 20]]


def test_skipped_and_mismatched_products(db: Db) -> None:
    db.post(10, "product")  # no group
    db.meta(10, _sku="A")
    db.post(11, "product")
    db.tr(11, "product", 5, "en")  # no sku
    _twins(db, 20, 200, "B")
    db.meta(20, _vendor_id="99", _legacy_lang="it")  # vendor and language disagree
    report = db.reader().products()
    assert report.skipped_no_group == [10]
    assert report.skipped_no_sku == [11]
    assert report.vendor_mismatches == [20]
    assert report.language_mismatches == [20]


def test_trashed_products_are_not_products(db: Db) -> None:
    _twins(db, 10, 100, "A", langs=("en",))
    db.c.execute(f"UPDATE {P}posts SET post_status = 'trash' WHERE id = 10")
    assert db.reader().products().products == []


# --- Redirect sources: seven, plus trashed products


def test_seven_redirect_sources(db: Db) -> None:
    assert len(rules.REDIRECT_SOURCE_KINDS) == 7
    _twins(db, 10, 100, "A", langs=("en",))
    db.meta(10, _legacy_old_slugs="old-a\nold-b,/old-a/", _wp_old_slug="core-old")
    db.post(11, "product", status="trash", name="gone__trashed")
    db.tr(11, "product", 101, "en")
    maps = RedirectCodeMaps(
        retired_skus={"r1": "/p/x/"},
        merged_tags_en={"t-en": "/tag/a/"},
        merged_tags_it={"t-it": "/tag/b/"},
        old_categories={"c": "/cat/"},
        old_brand_base={"b": "/brand/"},
    )
    out = db.reader().redirect_sources(maps)
    kinds = rules.RedirectSourceKind
    by_kind = {k: [s.old for s in out if s.kind == k] for k in kinds}
    assert by_kind[kinds.CUSTOM_OLD_SLUGS] == ["old-a", "old-b"]
    assert by_kind[kinds.CORE_OLD_SLUG] == ["core-old"]
    assert by_kind[kinds.TRASHED_PRODUCT] == ["gone"]
    for kind in rules.REDIRECT_SOURCE_KINDS:
        assert by_kind[kind], kind


def test_custom_old_slugs_capped_at_ten(db: Db) -> None:
    _twins(db, 10, 100, "A", langs=("en",))
    db.meta(10, _legacy_old_slugs=",".join(f"s{i}" for i in range(15)))
    out = db.reader().redirect_sources()
    assert len([s for s in out if s.kind == rules.RedirectSourceKind.CUSTOM_OLD_SLUGS]) == 10


# --- Money


@pytest.mark.parametrize(
    "raw", ["", " 1.00", "1,50", "1e3", "NaN", "Infinity", "1.2.3", "abc", "--1", None, 5]
)
def test_malformed_money_rejected(raw: object) -> None:
    with pytest.raises(rules.LegacyDataError):
        rules.parse_money(raw)


@pytest.mark.parametrize(
    ("raw", "want"),
    [("12.50", "12.50"), ("0", "0"), ("-3.10", "-3.10"), (".5", ".5"), ("8.2645", "8.2645")],
)
def test_money_parsed_exactly(raw: str, want: str) -> None:
    assert rules.parse_money(raw) == Decimal(want)


# --- Orders: posts store authoritative, drift check, fee guard, minimal read list


def test_orders_read_from_posts_and_drift_reported(db: Db) -> None:
    db.order(1)
    db.order(2, total="20.00")
    db.c.execute(f"UPDATE {P}orders_secondary SET total_amount = '21.00' WHERE id = 2")
    db.order(3, secondary=False)
    db.c.execute(f"INSERT INTO {P}orders_secondary VALUES (4, 'completed', '5.00')")
    db.order(5, status="refunded")
    db.c.execute(f"UPDATE {P}orders_secondary SET status = 'completed' WHERE id = 5")
    report = db.reader().orders()
    assert [o.order_id for o in report.orders] == [1, 2, 3, 5]
    assert next(o.total for o in report.orders if o.order_id == 2) == Decimal("20.00")  # posts wins
    assert {(d.order_id, d.kind) for d in report.drift} == {
        (2, "total"),
        (3, "missing_in_secondary"),
        (4, "missing_in_posts"),
        (5, "status"),
    }


def test_no_drift_when_stores_agree(db: Db) -> None:
    db.order(1)
    assert db.reader().orders().drift == []


def test_malformed_order_total_is_reported_not_defaulted(db: Db) -> None:
    db.order(1, total="12,5")
    report = db.reader().orders()
    assert report.orders == []
    assert report.malformed[0][0] == 1


def test_vat_like_fee_line_fails(db: Db) -> None:
    db.order(1)
    db.c.execute(f"INSERT INTO {P}order_items VALUES (1, 1, 'fee', 'VAT 22%')")
    with pytest.raises(rules.VatLikeFeeError):
        db.reader().orders()


def test_other_fee_and_tax_lines_pass(db: Db) -> None:
    db.order(1)
    db.c.execute(f"INSERT INTO {P}order_items VALUES (1, 1, 'fee', 'Handling')")
    db.c.execute(f"INSERT INTO {P}order_items VALUES (2, 1, 'tax', 'VAT')")
    assert db.reader().orders().malformed == []


def test_order_without_tax_line_is_legitimate(db: Db) -> None:
    db.order(1)
    assert [o.order_id for o in db.reader().orders().orders] == [1]


def test_tax_archive_read_list_excludes_personal_data(db: Db) -> None:
    secrets_ = {
        "_shipping_address_1": "s1",
        "_billing_phone": "p",
        "_billing_email": "e",
        "_customer_ip_address": "i",
        "_customer_user_agent": "u",
        "_payment_token": "t",
        "_order_note": "n",
    }
    db.order(
        1,
        extra={"_billing_first_name": "Ann", "_billing_vat_number": "XX123", **secrets_},
    )
    reader = db.reader()
    statements: list[str] = []
    reader._conn.set_trace_callback(statements.append)
    report = reader.orders()
    assert report.orders[0].meta["_billing_first_name"] == "Ann"
    assert report.orders[0].meta["_billing_vat_number"] == "XX123"
    assert set(report.orders[0].meta).isdisjoint(secrets_)
    joined = " ".join(statements).lower()
    for pattern in rules.FORBIDDEN_META_PATTERNS:
        assert pattern not in joined.replace("_order_shipping", "").replace("_billing_vat", ""), (
            pattern
        )


def test_read_list_is_checked_against_forbidden_patterns() -> None:
    rules.assert_tax_archive_keys(rules.TAX_ARCHIVE_ORDER_META)
    for bad in ("_shipping_address_1", "_billing_phone", "_customer_ip_address", "_unknown"):
        with pytest.raises(rules.LegacyDataError):
            rules.assert_tax_archive_keys([bad])


# --- Customers: role + activity rule


def _ts(dt: datetime) -> str:
    return str(int(dt.timestamp()))


def test_customer_rule(db: Db) -> None:
    db.user(1, "customer", _ts(datetime(2026, 6, 1, tzinfo=UTC)))  # active within 12 months
    db.user(2, "customer", _ts(datetime(2025, 6, 1, tzinfo=UTC)))  # too old, no order
    db.user(3, "customer")  # no activity at all
    db.user(4, "subscriber", _ts(datetime(2026, 11, 1, tzinfo=UTC)))  # spam role, recent meta
    db.user(5, 'a:1:{s:8:"customer";b:1;}')  # serialized role, order 20 months ago
    db.order(50, date="2025-03-01", user=5)
    db.user(6, "customer")  # order 25 months ago: too old
    db.order(51, date="2024-10-01", user=6)
    ids = [c.user_id for c in db.reader().customers(now=NOW)]
    assert ids == [1, 5]


def test_activity_window_is_an_input(db: Db) -> None:
    db.user(1, "customer", _ts(datetime(2025, 12, 1, tzinfo=UTC)))  # 11 months before NOW
    reader = db.reader()
    assert [c.user_id for c in reader.customers(now=NOW)] == [1]
    assert reader.customers(now=NOW, activity_window_months=6) == []


def test_months_before_clamps_day() -> None:
    assert rules.months_before(datetime(2026, 3, 31), 1) == datetime(2026, 2, 28)
    assert rules.months_before(datetime(2026, 1, 15), 24) == datetime(2024, 1, 15)
