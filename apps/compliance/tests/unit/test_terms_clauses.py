"""CMP.5: the contract clauses (a) to (i) as pure texts (no database)."""

import pytest

from apps.compliance import terms_clauses as tc


def test_customer_terms_carry_a_to_c_and_vendor_terms_d_to_h() -> None:
    assert [c.letter for c in tc.clauses_for(tc.CUSTOMER)] == ["a", "b", "c"]
    assert [c.letter for c in tc.clauses_for(tc.VENDOR)] == ["d", "e", "f", "g", "h", "i"]


@pytest.mark.parametrize("language", ["en", "it"])
def test_every_clause_has_a_heading_and_a_text_in_each_language(language: str) -> None:
    for clause in tc.CLAUSES:
        assert clause.headings[language].strip()
        assert clause.texts[language].strip()


def test_the_wording_states_the_elements_of_the_adviser_opinion() -> None:
    by_key = {c.key: c.texts["en"] for c in tc.CLAUSES}
    assert "art. 1188" in by_key["payment_discharge"]
    assert "art. 129" in by_key["garage_compatibility"]
    assert "Connected Account Agreement" in by_key["stripe_role"]
    assert "art. 1252" in by_key["set_off"]
    assert "SEPA" in by_key["set_off"]
    fee = by_key["refund_fee"]
    assert "EUR 2.00" in fee
    assert "15 days" in fee
    assert "platform malfunction or error" in fee
    assert "rounded up to the whole euro" in fee
    assert "accessible" in by_key["accessibility"]
    it = {c.key: c.texts["it"] for c in tc.CLAUSES}
    assert "art. 1188" in it["payment_discharge"]
    assert "EUR 2,00" in it["refund_fee"]
    assert "15 giorni" in it["refund_fee"]
    assert "art. 1252" in it["set_off"]


def test_the_owner_review_of_2026_11_13_is_in_the_wording() -> None:
    en = {c.letter: c.texts["en"] for c in tc.CLAUSES}
    it = {c.letter: c.texts["it"] for c in tc.CLAUSES}
    assert "For a consumer customer" in en["b"]
    assert "Per il Cliente consumatore" in it["b"]
    assert "la compatibilità universale" in it["c"]
    assert "stato universale" not in it["c"]
    assert en["d"].startswith("The platform does not provide payment services.")
    assert it["d"].startswith("La piattaforma non presta servizi di pagamento.")
    assert "importi rimborsati ai Clienti" in it["e"]
    assert "Stripe's actual cost" in en["f"]
    assert "costo vivo di Stripe" in it["f"]
    assert "D.Lgs. 82/2022" in it["g"]
    assert "Legislative Decree 82/2022" in en["g"]
    assert "manleva la piattaforma da reclami di terzi" in it["h"]
    assert "indemnifies the platform against third-party claims" in en["h"]
    assert "non riducono l'importo comunicato dalla piattaforma" in it["i"]
    assert "does not reduce the amount the platform reports" in en["i"]


@pytest.mark.parametrize("language", ["en", "it"])
def test_the_refund_fee_clause_has_no_pass_on_sentence(language: str) -> None:
    # Codice del consumo artt. 56-57: refund handling costs cannot be charged to the consumer.
    text = tc.CLAUSES[5].texts[language].lower()
    assert tc.CLAUSES[5].letter == "f"
    for phrase in ("pass it on", "return terms", "trasferirlo", "condizioni di reso"):
        assert phrase not in text
    amount = "EUR 2.00" if language == "en" else "EUR 2,00"
    assert tc.CLAUSES[5].texts[language].count(amount) == 1


def test_the_italian_texts_are_formal_not_informal() -> None:
    for clause in tc.CLAUSES:
        words = clause.texts["it"].lower().split()
        assert not {"tu", "ti", "tuo", "tua", "puoi", "devi"} & set(words)


def test_a_block_has_all_clauses_of_a_kind_and_none_of_the_other() -> None:
    block = tc.clause_block(tc.VENDOR, "en")
    assert block.startswith(tc.BLOCK_MARKER)
    assert tc.missing_clauses(tc.VENDOR, block, "en") == []
    assert tc.missing_clauses(tc.CUSTOMER, block, "en") == ["a", "b", "c"]


def test_a_changed_word_or_a_missing_clause_is_reported() -> None:
    block = tc.clause_block(tc.VENDOR, "en")
    assert tc.missing_clauses(tc.VENDOR, block.replace("EUR 2.00", "about EUR 2"), "en") == ["f"]
    assert tc.missing_clauses(tc.VENDOR, "", "en") == ["d", "e", "f", "g", "h", "i"]
    assert tc.missing_clauses(tc.VENDOR, "", "en", only=["g"]) == ["g"]


def test_line_breaks_and_spacing_do_not_matter() -> None:
    squashed = " ".join(tc.clause_block(tc.CUSTOMER, "it").split("\n")).replace(" ", "  ")
    assert tc.missing_clauses(tc.CUSTOMER, squashed, "it") == []


def test_a_new_block_replaces_the_old_one_and_keeps_the_text_before() -> None:
    first = tc.with_clauses(tc.VENDOR, "en", "Base terms.")
    second = tc.with_clauses(tc.VENDOR, "en", first)
    assert second == first
    assert second.startswith("Base terms.")
    assert second.count(tc.BLOCK_MARKER) == 1


def test_a_language_without_wording_uses_english() -> None:
    assert tc.clause_block(tc.VENDOR, "de") == tc.clause_block(tc.VENDOR, "en")
