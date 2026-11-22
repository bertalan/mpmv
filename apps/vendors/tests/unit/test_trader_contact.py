"""CMP.41: the per-seller trader contact of the withdrawal information (name, geographic
address, telephone, email; Art. 246a EGBGB Anlage 1, Gestaltungshinweis 2)."""

from apps.vendors import engine


def contact(**overrides: str) -> engine.TraderContact:
    fields = {
        "shop_name": "Alpha Shop",
        "legal_name": "Alpha Shop S.r.l.",
        "street": "Via Example",
        "street_number": "7",
        "postal_code": "00100",
        "city": "Rome",
        "country": "IT",
        "phone": "+39 06 5550 0000",
        "email": "shop@example.com",
    }
    fields.update(overrides)
    return engine.trader_contact(**fields)


def test_the_contact_carries_name_address_phone_and_email() -> None:
    found = contact()
    assert found.shop_name == "Alpha Shop"
    assert found.legal_name == "Alpha Shop S.r.l."
    assert found.address == "Via Example 7, 00100 Rome, IT"
    assert found.phone == "+39 06 5550 0000"
    assert found.email == "shop@example.com"


def test_the_legal_name_falls_back_to_the_shop_name() -> None:
    assert contact(legal_name="").legal_name == "Alpha Shop"


def test_a_seller_without_a_phone_keeps_it_empty_and_never_gets_one_invented() -> None:
    assert contact(phone="  ").phone == ""


def test_empty_address_parts_are_left_out() -> None:
    assert contact(street_number="", postal_code="").address == "Via Example, Rome, IT"
    assert contact(street="", street_number="", postal_code="", city="").address == "IT"


def test_the_contact_is_plain_data_for_an_email_context() -> None:
    assert set(contact().as_dict()) == {
        "shop_name",
        "legal_name",
        "address",
        "phone",
        "email",
    }
