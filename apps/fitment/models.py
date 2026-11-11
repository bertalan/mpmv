"""The fitment scheme and its nodes (D-38, spec 17 §17.2, FIT.1).

Platform data, shared by every vendor (no vendor scope, no personal data). The operator defines
the scheme: one to four node levels ("make", "model", "variant" in one sector, "brand", "series"
in another) and a tree of nodes under it. The levels are data, not code. Products declare what
they fit (FIT.2) and customers keep profiles (FIT.5) by pointing at a node id, so a node is only
ever merged (references moved, the old id kept as a redirect) or retired, never reused.

Trees are treebeard materialized-path nodes, like categories: a node's depth is its level's
position. Create and move nodes through `apps.fitment.services`, never by writing `path`.
"""

from typing import Any, ClassVar, Self

from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from treebeard.mp_tree import MP_Node, MP_NodeManager, MP_NodeQuerySet

from apps.catalog.models import Category
from apps.core.fields import LanguageField
from apps.core.models import BaseModel, TranslationModel

from . import engine

code_validator = RegexValidator(
    r"^[a-z][a-z0-9_-]*$", _("Use lower case letters, digits, - and _, starting with a letter.")
)


class FitmentScheme(BaseModel):
    """The installation's one scheme record: wording, year range and the garage limits.

    There is one row per installation, kept by `services.current_scheme`; the levels are in
    `FitmentLevel`.
    """

    garage_noun_key = models.CharField(
        _("garage noun"),
        max_length=60,
        default="Garage",
        help_text=_(
            "The word shown to customers for their saved items, for example Garage or My devices. "
            "It is translated with the site texts."
        ),
    )
    year_enabled = models.BooleanField(
        _("uses a year"),
        default=False,
        help_text=_("Turn on when a year or year range means something in this sector."),
    )
    year_min = models.SmallIntegerField(
        _("earliest year"),
        default=1900,
        help_text=_("The earliest year a seller or a customer can enter."),
    )
    year_max_offset = models.SmallIntegerField(
        _("latest year, in years from now"),
        default=2,
        help_text=_("How many years after the current year can be entered, for new models."),
    )
    contains_special_category = models.BooleanField(
        _("items reveal sensitive information"),
        default=False,
        help_text=_(
            "Turn on only when what a customer saves could reveal sensitive personal data, such "
            "as health. The garage is then kept for the visit only and not saved to the account."
        ),
    )
    max_profiles = models.PositiveSmallIntegerField(
        _("profiles per customer"),
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text=_("How many saved items one customer account can keep."),
    )

    class Meta:
        verbose_name = _("fitment scheme")

    def __str__(self) -> str:
        return str(_("Fitment scheme"))


class FitmentLevel(BaseModel):
    """One level of the scheme: its position is the depth of its nodes."""

    code = models.SlugField(
        _("code"),
        unique=True,
        validators=[code_validator],
        help_text=_("A short internal name, for example make or model. It never changes."),
    )
    position = models.PositiveSmallIntegerField(
        _("position"),
        unique=True,
        help_text=_("1 is the top level. A level can only be added at the end."),
    )
    translate_names = models.BooleanField(
        _("names differ by language"),
        default=False,
        help_text=_(
            "Turn on when the names at this level are written differently in each language. "
            "Make and model names are usually the same everywhere, so it is off by default."
        ),
    )

    class Meta:
        ordering = ["position"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                # The limit of four levels is the service's rule (`engine.add_level_error`); the
                # database only keeps positions positive, so a reorder can park levels above it.
                condition=models.Q(position__gte=1),
                name="fitment_level_position_positive",
            )
        ]

    def __str__(self) -> str:
        return self.code


class FitmentLevelTranslation(TranslationModel):
    """What a level is called, in one language: "Make", "Marke", "Marca"."""

    translation_of = "level"
    translated_fields = ("label", "plural_label", "placeholder")

    level = models.ForeignKey(FitmentLevel, on_delete=models.CASCADE, related_name="translations")
    label = models.CharField(
        _("label"), max_length=60, help_text=_("The level's name for one item, such as Make.")
    )
    plural_label = models.CharField(
        _("plural label"),
        max_length=60,
        help_text=_("The level's name for several, such as Makes."),
    )
    placeholder = models.CharField(
        _("placeholder"),
        max_length=100,
        blank=True,
        help_text=_("The hint shown in the empty picker, such as Choose a make."),
    )

    class Meta(TranslationModel.Meta):
        ordering = ["level", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["level", "language"], name="fitment_leveltranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.label} [{self.language}]"


class NodeStatus(models.TextChoices):
    PROPOSED = engine.PROPOSED, _("Proposed")
    ACTIVE = engine.ACTIVE, _("Active")
    RETIRED = engine.RETIRED, _("Retired")


class FitmentNodeQuerySet(MP_NodeQuerySet):  # type: ignore[misc]  # treebeard is untyped
    def pickable(self) -> Self:
        """What a picker offers: approved nodes that are not retired."""
        return self.filter(status=NodeStatus.ACTIVE)  # type: ignore[no-any-return]

    def proposals(self) -> Self:
        """The review queue of the Catalog and moderation role."""
        return self.filter(status=NodeStatus.PROPOSED)  # type: ignore[no-any-return]


class FitmentNodeManager(MP_NodeManager):  # type: ignore[misc]  # treebeard is untyped
    def get_queryset(self) -> FitmentNodeQuerySet:
        return FitmentNodeQuerySet(self.model).order_by("path")  # type: ignore[no-any-return]

    def pickable(self) -> FitmentNodeQuerySet:
        return self.get_queryset().pickable()

    def proposals(self) -> FitmentNodeQuerySet:
        return self.get_queryset().proposals()


class FitmentNode(MP_Node, BaseModel):  # type: ignore[misc]  # treebeard is untyped
    """A make, model, variant... Its tree depth equals its level's position."""

    Status = NodeStatus

    level = models.ForeignKey(
        FitmentLevel,
        on_delete=models.PROTECT,
        related_name="nodes",
        help_text=_(
            "The level of the scheme this node belongs to, set from its place in the tree."
        ),
    )
    name = models.CharField(
        _("name"),
        max_length=120,
        help_text=_(
            "The name as people write it. It is the same in every language unless the level "
            "translates names."
        ),
    )
    normalized_name = models.CharField(max_length=160, editable=False, db_index=True)
    sort_key = models.CharField(max_length=200, editable=False)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=NodeStatus.choices,
        default=NodeStatus.ACTIVE,
        db_index=True,
        help_text=_(
            "Proposed nodes wait for review and are not offered in pickers. Retired nodes stay "
            "in saved items but are no longer offered."
        ),
    )

    objects: ClassVar[FitmentNodeManager] = FitmentNodeManager()

    class Meta:
        ordering = ["path"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.name = engine.clean_name(self.name)
        self.normalized_name = engine.normalize_name(self.name)
        self.sort_key = engine.natural_sort_key(self.name)
        super().save(*args, **kwargs)


class FitmentNodeTranslation(TranslationModel):
    """A node's name in one language; used only when its level translates names."""

    translation_of = "node"
    translated_fields = ("name",)

    node = models.ForeignKey(FitmentNode, on_delete=models.CASCADE, related_name="translations")
    name = models.CharField(
        _("name"), max_length=120, help_text=_("The node's name written for this language.")
    )

    class Meta(TranslationModel.Meta):
        ordering = ["node", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["node", "language"], name="fitment_nodetranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} [{self.language}]"


class FitmentNodeAlias(BaseModel):
    """Another way to write a node's name: old names, typing variants. Found by search."""

    node = models.ForeignKey(FitmentNode, on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(
        _("alias"), max_length=120, help_text=_("Another way people write this name.")
    )
    normalized = models.CharField(max_length=160, editable=False, db_index=True)
    language = LanguageField(
        null=True,
        blank=True,
        help_text=_("Leave empty when the alias is used in every language."),
    )

    class Meta:
        ordering = ["node", "normalized"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["node", "normalized", "language"],
                nulls_distinct=False,
                name="fitment_nodealias_unique",
            )
        ]

    def __str__(self) -> str:
        return self.alias

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.alias = engine.clean_name(self.alias)
        self.normalized = engine.normalize_name(self.alias)
        super().save(*args, **kwargs)


class FitmentNodeMerge(BaseModel):
    """A merged node's old id, kept so saved references to it still find the node it became."""

    old_node_id = models.BigIntegerField(
        unique=True, help_text=_("The id the merged node had. Never reused.")
    )
    into_node = models.ForeignKey(
        FitmentNode,
        on_delete=models.CASCADE,
        related_name="merged_from",
        help_text=_("The node that replaced it."),
    )

    def __str__(self) -> str:
        return f"{self.old_node_id} -> {self.into_node_id}"


class FitmentNote(BaseModel):
    """A short operator-defined reason shown with a fitment row: "with adapter", "rear only"."""

    code = models.SlugField(
        _("code"),
        unique=True,
        validators=[code_validator],
        help_text=_("A short internal name for the note."),
    )

    def __str__(self) -> str:
        return self.code


class FitmentNoteTranslation(TranslationModel):
    translation_of = "note"
    translated_fields = ("text",)

    note = models.ForeignKey(FitmentNote, on_delete=models.CASCADE, related_name="translations")
    text = models.CharField(
        _("text"), max_length=120, help_text=_("The note as customers read it, in this language.")
    )

    class Meta(TranslationModel.Meta):
        ordering = ["note", "language"]
        constraints = [
            *TranslationModel.Meta.constraints,
            models.UniqueConstraint(
                fields=["note", "language"], name="fitment_notetranslation_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.text} [{self.language}]"


class FitmentCategory(BaseModel):
    """A product category whose items are expected to declare what they fit (spec 17 §17.2.3).

    The presence of a row is the setting: no row, no expectation, and the garage never hides an
    item of that category. It is a table of this app, not a field of `Category`, so the catalog
    does not know the garage. Seeded with no rows.
    """

    category = models.OneToOneField(
        Category,
        on_delete=models.CASCADE,
        related_name="fitment_setting",
        help_text=_("Items in this category must say which items they fit."),
    )

    class Meta:
        verbose_name_plural = "fitment categories"

    def __str__(self) -> str:
        return f"{self.category_id}"
