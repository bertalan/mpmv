"""SEC.4: which vendor an audit entry belongs to, for the staff view's vendor filter."""

from apps.core.audit import vendor_key_of

VENDOR_ID = "6f1c1f2e-0000-4000-8000-000000000001"


def test_an_entry_about_a_vendor_belongs_to_that_vendor() -> None:
    assert vendor_key_of("vendors.vendor", VENDOR_ID, {"status": "applicant"}, None) == VENDOR_ID


def test_a_vendor_owned_object_names_its_vendor_in_the_snapshot() -> None:
    after = {"vendor": VENDOR_ID, "user": "u-1", "role": "staff"}
    assert vendor_key_of("vendors.vendormember", "m-1", None, after) == VENDOR_ID


def test_a_deleted_vendor_owned_object_names_its_vendor_in_the_before_snapshot() -> None:
    before = {"vendor": VENDOR_ID, "role": "owner"}
    assert vendor_key_of("vendors.vendormember", "m-1", before, None) == VENDOR_ID


def test_platform_objects_belong_to_no_vendor() -> None:
    assert vendor_key_of("core.platformoperator", "p-1", None, {"country": "AT"}) == ""


def test_a_missing_vendor_value_is_ignored() -> None:
    assert vendor_key_of("shipping.zone", "z-1", None, {"vendor": None}) == ""
