"""VEN.18: fiscal regime RF20 (cross-border VAT franchise, Directive (EU) 2020/285, FatturaPA
specification 1.9) against the real database.

Done when: a vendor can save RF20; the vendor constraint accepts RF01-RF20 except RF03;
`fatturapa_party()` emits `RegimeFiscale` RF20.
"""

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.vendors import engine
from apps.vendors.models import FiscalRegime, Vendor
from apps.vendors.services import save_vendor
from apps.vendors.tests.factories import VendorFactory

CTX = AuditContext(actor_id=1, actor_label="staff:reviewer", source="admin", ip="127.0.0.1")
RF20 = engine.CROSS_BORDER_FRANCHISE_REGIME
ALL_REGIMES = [f"RF{n:02d}" for n in range(1, 21) if n != 3]

BEFORE = [("vendors", "0013_crossvendorconsent")]
AFTER = [("vendors", "0014_fiscal_regime_rf20")]


@pytest.mark.django_db
def test_rf20_is_a_choice_with_its_official_label() -> None:
    assert FiscalRegime.RF20 == "RF20"
    assert FiscalRegime.RF20.label == (
        "RF20 - Regime transfrontaliero di Franchigia IVA (Direttiva UE 2020/285)"
    )
    assert FiscalRegime.values == ALL_REGIMES


@pytest.mark.django_db
def test_a_vendor_can_save_rf20_and_the_change_is_audited() -> None:
    vendor = VendorFactory.create(italian_company=True)
    vendor.fiscal_regime = RF20
    vendor.cross_border_franchise_number = f"IT{vendor.vat_number}EX"  # VEN.25
    save_vendor(vendor, ctx=CTX)

    assert Vendor.objects.get(pk=vendor.pk).fiscal_regime == RF20
    entry = AuditLog.objects.get(action="vendor.updated")
    assert entry.after is not None
    assert entry.after["fiscal_regime"] == RF20


@pytest.mark.django_db
@pytest.mark.parametrize("regime", ALL_REGIMES)
def test_the_database_accepts_every_regime_of_the_specification(regime: str) -> None:
    vendor = VendorFactory.create()
    Vendor.objects.filter(pk=vendor.pk).update(fiscal_regime=regime)
    assert Vendor.objects.get(pk=vendor.pk).fiscal_regime == regime


@pytest.mark.django_db
@pytest.mark.parametrize("regime", ["RF03", "RF21", "RF00"])
def test_the_database_refuses_regimes_outside_the_specification(regime: str) -> None:
    vendor = VendorFactory.create()
    with pytest.raises(IntegrityError), transaction.atomic():
        Vendor.objects.filter(pk=vendor.pk).update(fiscal_regime=regime)


@pytest.mark.django_db
def test_an_rf20_vendor_fills_the_party_block_with_rf20() -> None:
    vendor = VendorFactory.create(italian_company=True, fiscal_regime=RF20)
    identity = Vendor.objects.get(pk=vendor.pk).legal_identity()
    cedente = engine.fatturapa_party(identity, engine.CEDENTE)
    assert cedente["DatiAnagrafici"]["RegimeFiscale"] == "RF20"


def _create_vendor(executor: MigrationExecutor, state: list[tuple[str, str]], regime: str) -> None:
    model = executor.loader.project_state(state).apps.get_model("vendors", "Vendor")
    model.objects.create(
        legal_name="Vendor A S.r.l.",
        display_name="Vendor A",
        slug=f"vendor-a-{regime.lower()}",
        country="IT",
        contact_email="vendor-a@example.com",
        fiscal_regime=regime,
    )


@pytest.mark.django_db(transaction=True)
def test_the_migration_widens_the_constraint_and_is_reversible() -> None:
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    with pytest.raises(IntegrityError), transaction.atomic():
        _create_vendor(executor, BEFORE, RF20)
    _create_vendor(executor, BEFORE, "RF19")

    executor.loader.build_graph()
    executor.migrate(AFTER)
    _create_vendor(executor, AFTER, RF20)
    model = executor.loader.project_state(AFTER).apps.get_model("vendors", "Vendor")
    assert sorted(model.objects.values_list("fiscal_regime", flat=True)) == ["RF19", RF20]

    # Back to RF01-RF19: refused while a vendor declares RF20, then fine without one.
    executor.loader.build_graph()
    with pytest.raises(IntegrityError):
        executor.migrate(BEFORE)
    model.objects.filter(fiscal_regime=RF20).delete()
    executor.loader.build_graph()
    executor.migrate(BEFORE)
    old = executor.loader.project_state(BEFORE).apps.get_model("vendors", "Vendor")
    assert list(old.objects.values_list("fiscal_regime", flat=True)) == ["RF19"]

    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())
