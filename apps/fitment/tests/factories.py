"""factory_boy factories for the fitment app (FIT.1). Synthetic values only.

`FitmentLevelFactory` takes the next free position (1 to 4). `FitmentNodeFactory` adds the node
with treebeard (`add_root`, or `add_child` of the `parent` declaration) and, unless `level` is
given, uses the level at its depth, creating it when the scheme has none yet.
"""

from typing import Any

import factory
from django.db.models import Max
from factory.django import DjangoModelFactory

from apps.catalog.tests.factories import CategoryFactory, TreeNodeCreation
from apps.core.models import TranslationStatus
from apps.fitment.models import (
    FitmentCategory,
    FitmentLevel,
    FitmentLevelTranslation,
    FitmentNode,
    FitmentNodeAlias,
    FitmentNodeMerge,
    FitmentNodeTranslation,
    FitmentNote,
    FitmentNoteTranslation,
    FitmentScheme,
)


def _next_position() -> int:
    top = FitmentLevel.objects.aggregate(top=Max("position"))["top"]
    return int(top or 0) + 1


class FitmentSchemeFactory(DjangoModelFactory[FitmentScheme]):
    class Meta:
        model = FitmentScheme

    garage_noun_key = "Garage"


class FitmentLevelFactory(DjangoModelFactory[FitmentLevel]):
    class Meta:
        model = FitmentLevel

    code = factory.Sequence(lambda n: f"level-{n}")
    position = factory.LazyFunction(_next_position)
    translate_names = False


class FitmentLevelTranslationFactory(DjangoModelFactory[FitmentLevelTranslation]):
    class Meta:
        model = FitmentLevelTranslation

    level = factory.SubFactory(FitmentLevelFactory)
    language = "en"
    status = TranslationStatus.PUBLISHED
    label = factory.Sequence(lambda n: f"Level {n}")
    plural_label = factory.Sequence(lambda n: f"Levels {n}")


class FitmentNodeFactory(TreeNodeCreation, DjangoModelFactory[FitmentNode]):
    """A root node, or a child of `parent`, on the level of its depth."""

    class Meta:
        model = FitmentNode

    name = factory.Sequence(lambda n: f"Node {n}")
    status = FitmentNode.Status.ACTIVE
    parent = None

    @classmethod
    def _create(cls, model_class: type[FitmentNode], *args: Any, **kwargs: Any) -> FitmentNode:
        parent = kwargs.get("parent")
        if "level" not in kwargs:
            depth = 1 if parent is None else parent.get_depth() + 1
            level = FitmentLevel.objects.filter(position=depth).first()
            kwargs["level"] = level or FitmentLevelFactory.create(position=depth)
        node: FitmentNode = super()._create(model_class, *args, **kwargs)
        return node

    @classmethod
    def _build(cls, model_class: type[FitmentNode], *args: Any, **kwargs: Any) -> FitmentNode:
        kwargs.setdefault("level", FitmentLevelFactory.build(position=1))
        node: FitmentNode = super()._build(model_class, *args, **kwargs)
        return node


class FitmentNodeTranslationFactory(DjangoModelFactory[FitmentNodeTranslation]):
    class Meta:
        model = FitmentNodeTranslation

    node = factory.SubFactory(FitmentNodeFactory)
    language = "it"
    status = TranslationStatus.PUBLISHED
    name = factory.Sequence(lambda n: f"Nodo {n}")


class FitmentNodeAliasFactory(DjangoModelFactory[FitmentNodeAlias]):
    class Meta:
        model = FitmentNodeAlias

    node = factory.SubFactory(FitmentNodeFactory)
    alias = factory.Sequence(lambda n: f"Alias {n}")


class FitmentNodeMergeFactory(DjangoModelFactory[FitmentNodeMerge]):
    class Meta:
        model = FitmentNodeMerge

    old_node_id = factory.Sequence(lambda n: 900_000 + n)
    into_node = factory.SubFactory(FitmentNodeFactory)


class FitmentNoteFactory(DjangoModelFactory[FitmentNote]):
    class Meta:
        model = FitmentNote

    code = factory.Sequence(lambda n: f"note-{n}")


class FitmentNoteTranslationFactory(DjangoModelFactory[FitmentNoteTranslation]):
    class Meta:
        model = FitmentNoteTranslation

    note = factory.SubFactory(FitmentNoteFactory)
    language = "en"
    status = TranslationStatus.PUBLISHED
    text = factory.Sequence(lambda n: f"Note text {n}")


class FitmentCategoryFactory(DjangoModelFactory[FitmentCategory]):
    class Meta:
        model = FitmentCategory

    category = factory.SubFactory(CategoryFactory)
