"""factory_boy factories for the fitment app (FIT.1). Synthetic values only.

`FitmentLevelFactory` counts positions from 1 (sequences restart in every test).
`FitmentNodeFactory` adds the node with treebeard (`add_root`, or `add_child` of the `parent`
declaration) and, unless `level` is given, uses the level at its depth,
creating it when the scheme has none yet.
"""

from typing import Any

import factory
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


class FitmentSchemeFactory(DjangoModelFactory[FitmentScheme]):
    """The installation's one scheme row (a database constraint): created once, then returned."""

    class Meta:
        model = FitmentScheme

    @classmethod
    def _create(cls, model_class: type[FitmentScheme], *args: Any, **kwargs: Any) -> FitmentScheme:
        scheme = model_class.objects.order_by("id").first()
        if scheme is None:
            scheme = model_class.objects.create(*args, **kwargs)
        return scheme

    garage_noun_key = "Garage"


class FitmentLevelFactory(DjangoModelFactory[FitmentLevel]):
    class Meta:
        model = FitmentLevel

    code = factory.Sequence(lambda n: f"level-{n}")
    position = factory.Sequence(lambda n: n + 1)  # sequences restart in every test
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
