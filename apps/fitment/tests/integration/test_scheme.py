"""FIT.1: the operator's scheme (levels) and its rules, in two sectors' vocabularies."""

import pytest
from django.db import IntegrityError, transaction

from apps.core.audit import AuditContext
from apps.core.models import AuditLog
from apps.fitment import services
from apps.fitment.models import FitmentLevel, FitmentLevelTranslation, FitmentScheme
from apps.fitment.services import FitmentError, LevelLabels
from apps.fitment.tests.factories import (
    FitmentLevelFactory,
    FitmentNodeFactory,
    FitmentSchemeFactory,
)

pytestmark = pytest.mark.django_db

CTX = AuditContext(actor_id=1, actor_label="staff:catalog", source="admin", ip="127.0.0.1")


def labels(singular: str, plural: str) -> dict[str, LevelLabels]:
    return {"en": LevelLabels(label=singular, plural_label=plural)}


def test_the_scheme_is_the_one_seeded_row_with_the_safe_defaults() -> None:
    scheme = services.current_scheme()
    assert scheme.contains_special_category is False
    assert scheme.max_profiles == 10
    assert FitmentScheme.objects.count() == 1
    assert services.current_scheme().pk == scheme.pk


def test_reading_the_scheme_never_creates_it() -> None:
    FitmentScheme.objects.all().delete()
    with pytest.raises(FitmentError):
        services.current_scheme()
    assert not FitmentScheme.objects.exists()


def test_a_two_level_scheme_in_one_sector_and_a_four_level_in_another() -> None:
    services.add_level(code="make", labels=labels("Make", "Makes"), ctx=CTX)
    services.add_level(code="model", labels=labels("Model", "Models"), ctx=CTX)
    assert list(FitmentLevel.objects.order_by("position").values_list("code", "position")) == [
        ("make", 1),
        ("model", 2),
    ]
    # A second installation, in another sector's words (no code change).
    FitmentLevel.objects.all().delete()
    for position, (code, one, many) in enumerate(
        [
            ("brand", "Brand", "Brands"),
            ("series", "Series", "Series"),
            ("model", "Model", "Models"),
            ("revision", "Revision", "Revisions"),
        ],
        start=1,
    ):
        applied = services.add_level(code=code, labels=labels(one, many), ctx=CTX)
        assert applied.obj is not None
        assert applied.obj.position == position
    assert FitmentLevelTranslation.objects.filter(language="en").count() == 4


def test_a_fifth_level_is_refused() -> None:
    for _ in range(4):
        FitmentLevelFactory.create()
    with pytest.raises(FitmentError):
        services.add_level(code="fifth", labels=labels("Fifth", "Fifths"), ctx=CTX)


def test_a_level_code_must_be_unique_and_a_slug() -> None:
    services.add_level(code="make", labels=labels("Make", "Makes"), ctx=CTX)
    with pytest.raises(FitmentError):
        services.add_level(code="make", labels=labels("Make", "Makes"), ctx=CTX)
    with pytest.raises(FitmentError):
        services.add_level(code="Not a slug", labels=labels("X", "Xs"), ctx=CTX)


def test_a_level_needs_a_label_in_the_source_language() -> None:
    with pytest.raises(FitmentError):
        services.add_level(code="make", labels={}, ctx=CTX)


def test_adding_a_level_is_a_dry_run_first_and_audited_when_applied() -> None:
    changes = services.add_level(
        code="make", labels=labels("Make", "Makes"), ctx=CTX, dry_run=True
    ).changes
    assert changes.summary()["create"] >= 1
    assert not FitmentLevel.objects.exists()
    services.add_level(code="make", labels=labels("Make", "Makes"), ctx=CTX)
    entry = AuditLog.objects.get(action="fitment.level.created")
    assert entry.target_model == "fitment.fitmentlevel"
    assert entry.actor_label == "staff:catalog"


def test_renaming_a_label_is_allowed_even_when_nodes_exist() -> None:
    level = services.add_level(code="make", labels=labels("Make", "Makes"), ctx=CTX).obj
    assert level is not None
    FitmentNodeFactory.create(level=level)
    services.rename_level(
        level, labels={"en": LevelLabels("Brand", "Brands", "Pick a brand")}, ctx=CTX
    )
    row = level.translations.get(language="en")
    assert (row.label, row.plural_label, row.placeholder) == ("Brand", "Brands", "Pick a brand")
    assert AuditLog.objects.filter(action="fitment.level.updated").exists()


def test_a_scheme_change_that_would_orphan_nodes_is_refused() -> None:
    make = services.add_level(code="make", labels=labels("Make", "Makes"), ctx=CTX).obj
    model = services.add_level(code="model", labels=labels("Model", "Models"), ctx=CTX).obj
    assert make is not None
    assert model is not None
    parent = FitmentNodeFactory.create(level=make)
    FitmentNodeFactory.create(level=model, parent=parent)
    with pytest.raises(FitmentError):
        services.reorder_levels(["model", "make"], ctx=CTX)
    with pytest.raises(FitmentError):
        services.remove_level(model, ctx=CTX)
    with pytest.raises(FitmentError):
        services.set_translate_names(make, True, ctx=CTX)
    assert list(FitmentLevel.objects.order_by("position").values_list("code", flat=True)) == [
        "make",
        "model",
    ]


def test_levels_without_nodes_can_be_reordered_and_the_last_removed() -> None:
    services.add_level(code="make", labels=labels("Make", "Makes"), ctx=CTX)
    services.add_level(code="model", labels=labels("Model", "Models"), ctx=CTX)
    services.reorder_levels(["model", "make"], ctx=CTX)
    assert list(FitmentLevel.objects.order_by("position").values_list("code", flat=True)) == [
        "model",
        "make",
    ]
    last = FitmentLevel.objects.get(code="make")
    services.remove_level(last, ctx=CTX)
    assert not FitmentLevel.objects.filter(code="make").exists()
    assert AuditLog.objects.filter(action="fitment.level.deleted").exists()


def test_the_database_keeps_level_positions_positive_and_unique() -> None:
    FitmentLevelFactory.create(position=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        FitmentLevelFactory.create(position=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        FitmentLevelFactory.create(position=0)


def test_the_year_range_and_profile_limits_are_validated_and_audited() -> None:
    services.save_scheme(
        {"year_enabled": True, "year_min": 1950, "year_max_offset": 2, "max_profiles": 5},
        ctx=CTX,
    )
    scheme = services.current_scheme()
    assert (scheme.year_enabled, scheme.year_min, scheme.max_profiles) == (True, 1950, 5)
    assert AuditLog.objects.filter(action="fitment.scheme.updated").exists()
    with pytest.raises(FitmentError):
        services.save_scheme({"max_profiles": 0}, ctx=CTX)
    with pytest.raises(FitmentError):
        services.save_scheme({"year_enabled": True, "year_min": 3000}, ctx=CTX)
    with pytest.raises(FitmentError):
        services.save_scheme({"unknown_field": 1}, ctx=CTX)


def test_a_value_of_the_wrong_type_is_a_fitment_error_not_a_type_error() -> None:
    for bad in (
        {"max_profiles": "five"},
        {"max_profiles": 2.5},
        {"max_profiles": True},
        {"year_enabled": "yes"},
        {"year_min": None},
        {"garage_noun_key": 7},
    ):
        with pytest.raises(FitmentError):
            services.save_scheme(bad, ctx=CTX)


def test_there_is_only_ever_one_scheme_row() -> None:
    scheme = services.current_scheme()
    with pytest.raises(IntegrityError), transaction.atomic():
        FitmentScheme.objects.create()
    assert services.current_scheme().pk == scheme.pk
    assert FitmentScheme.objects.count() == 1


def test_the_scheme_factory_returns_the_one_row() -> None:
    assert FitmentSchemeFactory.create().pk == FitmentSchemeFactory.create().pk
    assert FitmentScheme.objects.count() == 1


def test_a_scheme_save_dry_run_changes_nothing() -> None:
    before = services.current_scheme().max_profiles
    changes = services.save_scheme({"max_profiles": 3}, ctx=CTX, dry_run=True)
    assert not changes.is_empty
    assert services.current_scheme().max_profiles == before
    assert not AuditLog.objects.filter(action="fitment.scheme.updated").exists()
