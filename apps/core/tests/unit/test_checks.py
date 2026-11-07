"""I18N.2: the reuse contract of `TranslationModel`, enforced by a system check.

Every `*Translation` table (vendor now, catalog later) names the entity it translates, lists its
translated text fields, keeps one row per entity and language, and keeps the status constraint.
Models are declared in an isolated app registry: nothing is created in the database.
"""

from django.core import checks
from django.db import models
from django.test.utils import isolate_apps

from apps.core.checks import check_translation_model, translation_models
from apps.core.models import TranslationModel


def ids(messages: list[checks.CheckMessage]) -> list[str]:
    return [message.id or "" for message in messages]


@isolate_apps("apps.core")
def test_a_model_that_follows_the_contract_passes() -> None:
    class Thing(models.Model):  # noqa: DJ008
        class Meta:
            app_label = "core"

    class ThingTranslation(TranslationModel):
        translation_of = "thing"
        translated_fields = ("name", "body")

        thing = models.ForeignKey(Thing, on_delete=models.CASCADE, related_name="translations")
        name = models.CharField(max_length=100)
        body = models.TextField(blank=True)

        class Meta(TranslationModel.Meta):
            app_label = "core"
            constraints = [
                *TranslationModel.Meta.constraints,
                models.UniqueConstraint(
                    fields=["thing", "language"], name="core_thingtranslation_unique"
                ),
            ]

    assert check_translation_model(ThingTranslation) == []


@isolate_apps("apps.core")
def test_missing_parent_fields_and_constraints_are_reported() -> None:
    class Thing(models.Model):  # noqa: DJ008
        class Meta:
            app_label = "core"

    class BrokenTranslation(TranslationModel):
        translation_of = "nothing"
        translated_fields = ("missing", "count")

        thing = models.ForeignKey(Thing, on_delete=models.CASCADE)
        count = models.IntegerField(default=0)

        class Meta:
            app_label = "core"

    assert ids(check_translation_model(BrokenTranslation)) == [
        "core.E101",
        "core.E102",
        "core.E103",
        "core.E104",
    ]


@isolate_apps("apps.core")
def test_empty_translated_fields_are_reported() -> None:
    class Thing(models.Model):  # noqa: DJ008
        class Meta:
            app_label = "core"

    class EmptyTranslation(TranslationModel):
        translation_of = "thing"

        thing = models.ForeignKey(Thing, on_delete=models.CASCADE)

        class Meta(TranslationModel.Meta):
            app_label = "core"
            constraints = [
                *TranslationModel.Meta.constraints,
                models.UniqueConstraint(fields=["thing", "language"], name="core_empty_unique"),
            ]

    assert ids(check_translation_model(EmptyTranslation)) == ["core.E102"]


def test_the_project_translation_models_pass_the_check() -> None:
    assert translation_models(None) == []
