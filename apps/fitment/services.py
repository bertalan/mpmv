"""Services of the fitment scheme and its nodes (D-38, spec 17 §17.2, FIT.1).

The views of the Manager platform pages, commands and later the import are thin adapters over
these functions. Every mutation takes the actor's `AuditContext`, accepts `dry_run=True` (the
`ChangeSet` is returned and nothing is written) and writes one `AuditLog` entry per change.
Refusals raise `FitmentError`, whose message is safe to show to staff.

Who may call them (Configuration and tax with step-up for the scheme, Catalog and moderation for
the nodes) is the adapter's job (CORE.18), never decided here.

Other parts of the garage add what points at a node (product rows, customer profiles) through
`apps.fitment.references`, so a merge moves them and a delete is refused while they exist.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.backends.postgresql.psycopg_any import NumericRange
from django.db.models import Count
from django.http import HttpRequest
from django.utils import timezone

from apps.catalog.models import Category, Product
from apps.core import translations
from apps.core.audit import AuditContext, record
from apps.core.changeset import ChangeSet
from apps.core.models import AuditLog, TranslationStatus
from apps.core.services import run_mutation
from apps.vendors import services as vendors_services
from apps.vendors.scoping import is_platform_staff

from . import engine, garage, references
from .models import (
    FitmentCategory,
    FitmentLevel,
    FitmentLevelTranslation,
    FitmentNode,
    FitmentNodeAlias,
    FitmentNodeMerge,
    FitmentNodeTranslation,
    FitmentNote,
    FitmentScheme,
    NodeStatus,
    ProductFitment,
    ProductFitmentState,
    code_validator,
)

SCHEME_UPDATED = "fitment.scheme.updated"
LEVEL_CREATED = "fitment.level.created"
LEVEL_UPDATED = "fitment.level.updated"
LEVEL_DELETED = "fitment.level.deleted"
NODE_CREATED = "fitment.node.created"
NODE_UPDATED = "fitment.node.updated"
NODE_STATUS_CHANGED = "fitment.node.status_changed"
NODE_MERGED = "fitment.node.merged"
NODE_REFUSED = "fitment.node.refused"
NODE_DELETED = "fitment.node.deleted"
ALIAS_CREATED = "fitment.alias.created"
ALIAS_DELETED = "fitment.alias.deleted"
CATEGORY_RELEVANCE_CHANGED = "fitment.category.relevance_changed"
PRODUCT_FITMENT_SET = "fitment.product.fitment_set"

SCHEME_FIELDS = (
    "garage_noun_key",
    "year_enabled",
    "year_min",
    "year_max_offset",
    "contains_special_category",
    "max_profiles",
)
MAX_NAME_LENGTH = 120


class FitmentError(ValueError):
    """A change the rules refuse; the message says why, for staff."""


@dataclass(frozen=True)
class LevelLabels:
    label: str
    plural_label: str
    placeholder: str = ""


@dataclass
class Applied[T]:
    """What a creating service returns: the ChangeSet, and the object once it is written."""

    changes: ChangeSet
    obj: T | None = None


# --- Scheme


def current_scheme() -> FitmentScheme:
    """The installation's scheme record: a read. The one row is seeded by migration 0001 with the
    defaults, and the database allows no second row."""
    scheme = FitmentScheme.objects.order_by("id").first()
    if scheme is None:
        raise FitmentError("The fitment scheme row is missing: run the migrations.")
    return scheme


def _lock_scheme() -> FitmentScheme:
    """Serialise scheme and node changes: they are rare and made by staff."""
    return FitmentScheme.objects.select_for_update().get(pk=current_scheme().pk)


def _scheme_snapshot(scheme: FitmentScheme) -> dict[str, Any]:
    return {name: getattr(scheme, name) for name in SCHEME_FIELDS}


def save_scheme(
    values: Mapping[str, Any], *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Change the wording, year range and limits of the scheme (Configuration and tax)."""
    unknown = sorted(set(values) - set(SCHEME_FIELDS))
    if unknown:
        raise FitmentError(f"Unknown scheme setting: {', '.join(unknown)}.")
    _check_types(values)
    with transaction.atomic():
        scheme = _lock_scheme()
        before = _scheme_snapshot(scheme)
        candidate = FitmentScheme(**{**before, **values})
        _validate_scheme(candidate)
        after = _scheme_snapshot(candidate)
        changeset = ChangeSet()
        changeset.update(scheme._meta.label_lower, str(scheme.public_id), before, after)

        def apply() -> None:
            FitmentScheme.objects.filter(pk=scheme.pk).update(**after)

        return run_mutation(changeset, apply, ctx=ctx, action=SCHEME_UPDATED, dry_run=dry_run)


_SCHEME_TYPES: dict[str, type] = {
    "garage_noun_key": str,
    "year_enabled": bool,
    "year_min": int,
    "year_max_offset": int,
    "contains_special_category": bool,
    "max_profiles": int,
}


def _check_types(values: Mapping[str, Any]) -> None:
    """Refuse a value of the wrong type with a `FitmentError`, never a `TypeError` later."""
    for name, value in values.items():
        expected = _SCHEME_TYPES[name]
        if type(value) is not expected:  # a bool is not a number here
            raise FitmentError(f"The setting {name} must be a {expected.__name__}.")


def _validate_scheme(scheme: FitmentScheme) -> None:
    if not 1 <= scheme.max_profiles <= 50:
        raise FitmentError("Profiles per customer must be between 1 and 50.")
    if not 1000 <= scheme.year_min <= 2100:
        raise FitmentError("The earliest year must be between 1000 and 2100.")
    if not 0 <= scheme.year_max_offset <= 20:
        raise FitmentError("The latest year must be 0 to 20 years from now.")
    if not scheme.garage_noun_key.strip():
        raise FitmentError("The garage noun cannot be empty.")


# --- Levels


def _level_labels(level: FitmentLevel) -> dict[str, dict[str, str]]:
    return {
        row.language: {
            "label": row.label,
            "plural_label": row.plural_label,
            "placeholder": row.placeholder,
        }
        for row in level.translations.all()
    }


def _labels_dict(labels: Mapping[str, LevelLabels]) -> dict[str, dict[str, str]]:
    known = {code for code, _name in settings.LANGUAGES}
    result: dict[str, dict[str, str]] = {}
    for language, text in labels.items():
        if language not in known:
            raise FitmentError(f"Unknown language: {language}.")
        label, plural = text.label.strip(), text.plural_label.strip()
        if not label or not plural:
            raise FitmentError("A level needs a label and a plural label in every language given.")
        result[language] = {
            "label": label,
            "plural_label": plural,
            "placeholder": text.placeholder.strip(),
        }
    return result


def _nodes_per_level() -> dict[str, int]:
    rows = FitmentNode.objects.order_by().values("level__code").annotate(total=Count("pk"))
    return {row["level__code"]: row["total"] for row in rows}


def _write_labels(level: FitmentLevel, labels: Mapping[str, Mapping[str, str]]) -> None:
    for language, text in labels.items():
        row = level.translations.filter(language=language).first() or FitmentLevelTranslation(
            level=level, language=language
        )
        row.label, row.plural_label, row.placeholder = (
            text["label"],
            text["plural_label"],
            text["placeholder"],
        )
        row.status = TranslationStatus.PUBLISHED  # typed by staff, not machine-made
        row.save()


def add_level(
    *,
    code: str,
    labels: Mapping[str, LevelLabels],
    translate_names: bool = False,
    ctx: AuditContext,
    dry_run: bool = False,
) -> Applied[FitmentLevel]:
    """Add a level at the end of the scheme (at most four)."""
    try:
        code_validator(code)
    except ValidationError as error:
        raise FitmentError("The code must be lower case letters, digits, - and _.") from error
    texts = _labels_dict(labels)
    if translations.source_language() not in texts:
        raise FitmentError("A level needs a label in the source language.")
    with transaction.atomic():
        _lock_scheme()
        positions = list(FitmentLevel.objects.values_list("position", flat=True))
        if error_text := engine.add_level_error(positions):
            raise FitmentError(error_text)
        if FitmentLevel.objects.filter(code=code).exists():
            raise FitmentError("A level with this code already exists.")
        position = max(positions, default=0) + 1
        after = {
            "code": code,
            "position": position,
            "translate_names": translate_names,
            "labels": texts,
        }
        changeset = ChangeSet()
        changeset.create("fitment.fitmentlevel", code, after)
        applied: Applied[FitmentLevel] = Applied(changeset)

        def apply() -> None:
            level = FitmentLevel.objects.create(
                code=code, position=position, translate_names=translate_names
            )
            _write_labels(level, texts)
            applied.obj = level

        applied.changes = run_mutation(
            changeset, apply, ctx=ctx, action=LEVEL_CREATED, dry_run=dry_run
        )
        return applied


def rename_level(
    level: FitmentLevel,
    *,
    labels: Mapping[str, LevelLabels],
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Change the labels of a level (allowed with nodes on it: the nodes do not move)."""
    texts = _labels_dict(labels)
    with transaction.atomic():
        _lock_scheme()
        before = _level_labels(level)
        merged = {**before, **texts}
        if translations.source_language() not in merged:
            raise FitmentError("A level needs a label in the source language.")
        changeset = ChangeSet()
        changeset.update("fitment.fitmentlevel", level.code, {"labels": before}, {"labels": merged})
        return run_mutation(
            changeset,
            lambda: _write_labels(level, texts),
            ctx=ctx,
            action=LEVEL_UPDATED,
            dry_run=dry_run,
        )


def set_translate_names(
    level: FitmentLevel, value: bool, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Choose whether names at this level differ by language; refused once it has nodes."""
    with transaction.atomic():
        _lock_scheme()
        if level.translate_names == value:
            return ChangeSet()
        if _nodes_per_level().get(level.code, 0):
            raise FitmentError("This level has nodes: its naming cannot change.")
        changeset = ChangeSet()
        changeset.update(
            "fitment.fitmentlevel",
            level.code,
            {"translate_names": level.translate_names},
            {"translate_names": value},
        )

        def apply() -> None:
            FitmentLevel.objects.filter(pk=level.pk).update(translate_names=value)
            level.translate_names = value

        return run_mutation(changeset, apply, ctx=ctx, action=LEVEL_UPDATED, dry_run=dry_run)


def remove_level(level: FitmentLevel, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Remove the last level when no node sits on it."""
    with transaction.atomic():
        _lock_scheme()
        codes = list(FitmentLevel.objects.order_by("position").values_list("code", flat=True))
        if error_text := engine.remove_level_error(codes, level.code, _nodes_per_level()):
            raise FitmentError(error_text)
        changeset = ChangeSet()
        changeset.delete(
            "fitment.fitmentlevel",
            level.code,
            {"code": level.code, "position": level.position, "labels": _level_labels(level)},
        )
        return run_mutation(changeset, level.delete, ctx=ctx, action=LEVEL_DELETED, dry_run=dry_run)


def reorder_levels(codes: Iterable[str], *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Put the levels in a new order, while no level that moves has nodes."""
    new = list(codes)
    with transaction.atomic():
        _lock_scheme()
        levels = list(FitmentLevel.objects.order_by("position"))
        current = [level.code for level in levels]
        if error_text := engine.reorder_error(current, new, _nodes_per_level()):
            raise FitmentError(error_text)
        by_code = {level.code: level for level in levels}
        changeset = ChangeSet()
        for position, code in enumerate(new, start=1):
            if by_code[code].position != position:
                changeset.update(
                    "fitment.fitmentlevel",
                    code,
                    {"position": by_code[code].position},
                    {"position": position},
                )

        def apply() -> None:
            parked = engine.MAX_LEVELS + 1
            for offset, level in enumerate(levels):  # park first: positions are unique
                FitmentLevel.objects.filter(pk=level.pk).update(position=parked + offset)
            for position, code in enumerate(new, start=1):
                FitmentLevel.objects.filter(pk=by_code[code].pk).update(position=position)

        return run_mutation(changeset, apply, ctx=ctx, action=LEVEL_UPDATED, dry_run=dry_run)


def resolve_node(node_id: int) -> FitmentNode | None:
    """The node an id stands for: itself, or the node it was merged into, or None."""
    node: FitmentNode | None = FitmentNode.objects.filter(pk=node_id).first()
    if node is not None:
        return node
    merge = FitmentNodeMerge.objects.filter(old_node_id=node_id).select_related("into_node").first()
    return merge.into_node if merge else None


# --- Nodes


def _siblings(parent: FitmentNode | None) -> list[FitmentNode]:
    return list(parent.get_children() if parent else FitmentNode.get_root_nodes())


def _taken_names(parent: FitmentNode | None, *, skip: FitmentNode | None = None) -> dict[str, int]:
    """Display names and aliases used among the children of `parent`, with the node's id."""
    taken: dict[str, int] = {}
    for sibling in _siblings(parent):
        if skip is not None and sibling.pk == skip.pk:
            continue
        taken[sibling.name] = sibling.pk
        for alias in sibling.aliases.all():
            taken[alias.alias] = sibling.pk
    return taken


def _checked_name(name: str) -> str:
    cleaned = engine.clean_name(name)
    if not engine.normalize_name(cleaned):
        raise FitmentError("A name needs at least one letter or digit.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise FitmentError(f"A name has at most {MAX_NAME_LENGTH} characters.")
    return cleaned


def _node_snapshot(node: FitmentNode) -> dict[str, Any]:
    return {"name": node.name, "level": node.level.code, "status": node.status}


def _parent_of(node: FitmentNode) -> FitmentNode | None:
    parent: FitmentNode | None = node.get_parent()
    return parent


def create_node(
    *,
    name: str,
    parent: FitmentNode | None = None,
    status: str = engine.ACTIVE,
    ctx: AuditContext,
    dry_run: bool = False,
) -> Applied[FitmentNode]:
    """Add a node under `parent` (or at the top). An active node needs an active parent."""
    if status not in (engine.ACTIVE, engine.PROPOSED):
        raise FitmentError("A new node is active or proposed.")
    cleaned = _checked_name(name)
    with transaction.atomic():
        _lock_scheme()
        if parent is not None:
            parent.refresh_from_db()
        depth = 1 if parent is None else parent.get_depth() + 1
        level = FitmentLevel.objects.filter(position=depth).first()
        if level is None:
            raise FitmentError("The scheme has no level for this depth.")
        allowed_parents = (
            {engine.ACTIVE} if status == engine.ACTIVE else {engine.ACTIVE, engine.PROPOSED}
        )
        if parent is not None and parent.status not in allowed_parents:
            raise FitmentError("The parent node must be approved first.")
        if engine.name_clash(cleaned, _taken_names(parent)) is not None:
            raise FitmentError("A node with this name or alias already exists at this level.")
        changeset = ChangeSet()
        changeset.create(
            "fitment.fitmentnode",
            cleaned,
            {
                "name": cleaned,
                "level": level.code,
                "status": status,
                "parent": parent.name if parent else None,
            },
        )
        applied: Applied[FitmentNode] = Applied(changeset)

        def apply() -> None:
            fields = {"name": cleaned, "level": level, "status": status}
            applied.obj = (
                FitmentNode.add_root(**fields) if parent is None else parent.add_child(**fields)
            )

        applied.changes = run_mutation(
            changeset, apply, ctx=ctx, action=NODE_CREATED, dry_run=dry_run
        )
        return applied


def propose_node(
    *,
    name: str,
    parent: FitmentNode | None = None,
    ctx: AuditContext,
    dry_run: bool = False,
) -> Applied[FitmentNode]:
    """A node waiting for review: invisible to pickers until `approve_node`."""
    return create_node(name=name, parent=parent, status=engine.PROPOSED, ctx=ctx, dry_run=dry_run)


def _change_status(
    nodes: list[FitmentNode], new: str, *, ctx: AuditContext, dry_run: bool
) -> ChangeSet:
    changeset = ChangeSet()
    for node in nodes:
        if error_text := engine.status_transition_error(node.status, new):
            raise FitmentError(error_text)
        changeset.update(
            "fitment.fitmentnode",
            str(node.public_id),
            {"name": node.name, "status": node.status},
            {"name": node.name, "status": new},
        )

    def apply() -> None:
        FitmentNode.objects.filter(pk__in=[n.pk for n in nodes]).update(status=new)

    return run_mutation(changeset, apply, ctx=ctx, action=NODE_STATUS_CHANGED, dry_run=dry_run)


def approve_node(node: FitmentNode, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    with transaction.atomic():
        _lock_scheme()
        node.refresh_from_db()
        parent = _parent_of(node)
        if parent is not None and parent.status != engine.ACTIVE:
            raise FitmentError("The parent node must be approved first.")
        return _change_status([node], engine.ACTIVE, ctx=ctx, dry_run=dry_run)


def refuse_node(
    node: FitmentNode, *, reason: str, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Refuse a proposal: the node and its proposed subtree are removed, the reason is audited."""
    if not reason.strip():
        raise FitmentError("A refusal needs a reason.")
    with transaction.atomic():
        _lock_scheme()
        node.refresh_from_db()
        subtree = [node, *node.get_descendants()]
        if any(n.status != engine.PROPOSED for n in subtree):
            raise FitmentError("Only proposed nodes can be refused.")
        if any(references.reference_count(n) for n in subtree):
            raise FitmentError("Something still refers to this node.")
        changeset = ChangeSet()
        changeset.delete("fitment.fitmentnode", str(node.public_id), _node_snapshot(node))
        if dry_run:
            return changeset
        key, before = str(node.public_id), _node_snapshot(node)
        node.delete()
        record(ctx, NODE_REFUSED, "fitment.fitmentnode", key, before, {"reason": reason.strip()})
        return changeset


def retire_node(node: FitmentNode, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Hide a node and its subtree from pickers; saved items keep pointing at them."""
    with transaction.atomic():
        _lock_scheme()
        node.refresh_from_db()
        subtree = [n for n in (node, *node.get_descendants()) if n.status == engine.ACTIVE]
        if not subtree:
            raise FitmentError("Only an active node can be retired.")
        return _change_status(subtree, engine.RETIRED, ctx=ctx, dry_run=dry_run)


def reactivate_node(node: FitmentNode, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Offer a retired node again (its own place only: its subtree stays as it is)."""
    with transaction.atomic():
        _lock_scheme()
        node.refresh_from_db()
        parent = _parent_of(node)
        if parent is not None and parent.status != engine.ACTIVE:
            raise FitmentError("The parent node is not active.")
        return _change_status([node], engine.ACTIVE, ctx=ctx, dry_run=dry_run)


def delete_node(node: FitmentNode, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    """Delete a node that has no children and that nothing refers to."""
    with transaction.atomic():
        _lock_scheme()
        node.refresh_from_db()
        if node.get_children_count():
            raise FitmentError("This node has children.")
        if references.reference_count(node):
            raise FitmentError("Something still refers to this node: merge it instead.")
        changeset = ChangeSet()
        changeset.delete("fitment.fitmentnode", str(node.public_id), _node_snapshot(node))
        return run_mutation(changeset, node.delete, ctx=ctx, action=NODE_DELETED, dry_run=dry_run)


def rename_node(
    node: FitmentNode, new_name: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Change the canonical name; the old one stays findable as an alias."""
    cleaned = _checked_name(new_name)
    with transaction.atomic():
        _lock_scheme()
        node.refresh_from_db()
        if engine.name_clash(cleaned, _taken_names(_parent_of(node), skip=node)) is not None:
            raise FitmentError("A node with this name or alias already exists at this level.")
        old = node.name
        keep_old = engine.normalize_name(old) != engine.normalize_name(cleaned)
        changeset = ChangeSet()
        changeset.update(
            "fitment.fitmentnode", str(node.public_id), {"name": old}, {"name": cleaned}
        )

        def apply() -> None:
            node.name = cleaned
            node.save()
            if keep_old:
                FitmentNodeAlias.objects.get_or_create(
                    node=node, alias=old, normalized=engine.normalize_name(old), language=None
                )

        return run_mutation(changeset, apply, ctx=ctx, action=NODE_UPDATED, dry_run=dry_run)


def set_node_name(
    node: FitmentNode, language: str, name: str, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """A node's name for one language, for levels that translate names."""
    cleaned = _checked_name(name)
    if language not in {code for code, _label in settings.LANGUAGES}:
        raise FitmentError(f"Unknown language: {language}.")
    with transaction.atomic():
        _lock_scheme()
        if not node.level.translate_names:
            raise FitmentError("This level does not translate names.")
        row = node.translations.filter(language=language).first()
        changeset = ChangeSet()
        key = f"{node.public_id}/{language}"
        if row is None:
            changeset.create("fitment.fitmentnodetranslation", key, {"name": cleaned})
        else:
            changeset.update(
                "fitment.fitmentnodetranslation", key, {"name": row.name}, {"name": cleaned}
            )

        def apply() -> None:
            target = row or FitmentNodeTranslation(node=node, language=language)
            target.name = cleaned
            target.status = TranslationStatus.PUBLISHED
            target.save()

        return run_mutation(changeset, apply, ctx=ctx, action=NODE_UPDATED, dry_run=dry_run)


def add_alias(
    node: FitmentNode,
    alias: str,
    *,
    language: str | None = None,
    ctx: AuditContext,
    dry_run: bool = False,
) -> Applied[FitmentNodeAlias]:
    cleaned = _checked_name(alias)
    with transaction.atomic():
        _lock_scheme()
        node.refresh_from_db()
        taken = _taken_names(_parent_of(node))
        taken[node.name] = node.pk
        if engine.name_clash(cleaned, taken) is not None:
            raise FitmentError("This name or alias is already used at this level.")
        changeset = ChangeSet()
        changeset.create(
            "fitment.fitmentnodealias",
            str(node.public_id),
            {"node": node.name, "alias": cleaned, "language": language},
        )
        applied: Applied[FitmentNodeAlias] = Applied(changeset)

        def apply() -> None:
            applied.obj = FitmentNodeAlias.objects.create(
                node=node, alias=cleaned, language=language
            )

        applied.changes = run_mutation(
            changeset, apply, ctx=ctx, action=ALIAS_CREATED, dry_run=dry_run
        )
        return applied


def remove_alias(alias: FitmentNodeAlias, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    changeset = ChangeSet()
    changeset.delete(
        "fitment.fitmentnodealias",
        str(alias.node.public_id),
        {"node": alias.node.name, "alias": alias.alias, "language": alias.language},
    )
    return run_mutation(changeset, alias.delete, ctx=ctx, action=ALIAS_DELETED, dry_run=dry_run)


def merge_nodes(
    source: FitmentNode, target: FitmentNode, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Merge `source` into `target`: children, aliases and every registered reference move to
    the target, the source's old id keeps answering as the target (`resolve_node`), and the
    source is deleted. All in one transaction; a name clash among the children refuses it."""
    with transaction.atomic():
        _lock_scheme()
        source.refresh_from_db()
        target.refresh_from_db()
        if error_text := engine.merge_error(
            source.path, target.path, source.get_depth(), target.get_depth()
        ):
            raise FitmentError(error_text)
        if target.status != engine.ACTIVE:
            raise FitmentError("Nodes are merged into an active node.")
        children = list(source.get_children())
        target_names = _taken_names(target)
        for child in children:
            if engine.name_clash(child.name, target_names) is not None:
                raise FitmentError(
                    f"The child {child.name!r} clashes with a child of the target: merge it first."
                )
        moved_references = references.reference_counts(source)
        # The source's name and aliases become aliases of the target, unless the target already
        # answers to them or a sibling of the target uses them: those are skipped, and listed.
        taken_by_others = _taken_names(_parent_of(target), skip=source)
        in_use = {engine.normalize_name(name) for name in taken_by_others}
        in_use |= {target.normalized_name}
        in_use |= set(target.aliases.values_list("normalized", flat=True))
        added: dict[str, str] = {}
        skipped: list[str] = []
        for name in [source.name, *source.aliases.values_list("alias", flat=True)]:
            normalized = engine.normalize_name(name)
            if normalized in added:
                continue
            if normalized in in_use:
                skipped.append(normalized)
            else:
                added[normalized] = name
        changeset = ChangeSet()
        changeset.update(
            "fitment.fitmentnode",
            str(source.public_id),
            {
                **_node_snapshot(source),
                "moved_children": len(children),
                "moved_references": moved_references,
            },
            {
                "merged_into": str(target.public_id),
                "added_aliases": list(added),
                "skipped_aliases": skipped,
            },
        )

        def apply() -> None:
            for child in children:
                child.refresh_from_db()
                child.move(FitmentNode.objects.get(pk=target.pk), pos="last-child")
            references.move_references(source, target)
            for name in added.values():
                FitmentNodeAlias.objects.create(node=target, alias=name, language=None)
            FitmentNodeMerge.objects.create(old_node_id=source.pk, into_node=target)
            FitmentNode.objects.get(pk=source.pk).delete()

        return run_mutation(changeset, apply, ctx=ctx, action=NODE_MERGED, dry_run=dry_run)


# --- Categories expected to declare fitment (an operator setting, none by default)


def is_fitment_relevant(category: Category) -> bool:
    return FitmentCategory.objects.filter(category=category).exists()


def relevant_category_ids() -> set[int]:
    return set(FitmentCategory.objects.values_list("category_id", flat=True))


def set_fitment_relevant(
    categories: Iterable[Category], relevant: bool, *, ctx: AuditContext, dry_run: bool = False
) -> ChangeSet:
    """Mark categories as expected to declare fitment, or not; any number at once."""
    chosen = list(categories)
    with transaction.atomic():
        current = relevant_category_ids()
        changeset = ChangeSet()
        todo: list[Category] = []
        for category in chosen:
            if (category.pk in current) == relevant:
                continue
            todo.append(category)
            snapshot = {"category": str(category.public_id), "relevant": relevant}
            before = {"category": str(category.public_id), "relevant": not relevant}
            if relevant:
                changeset.create("fitment.fitmentcategory", str(category.public_id), snapshot)
            else:
                changeset.delete("fitment.fitmentcategory", str(category.public_id), before)

        def apply() -> None:
            if relevant:
                FitmentCategory.objects.bulk_create(
                    [FitmentCategory(category=category) for category in todo]
                )
            else:
                FitmentCategory.objects.filter(category__in=todo).delete()
            _refresh_always_visible(categories=todo)

        return run_mutation(
            changeset, apply, ctx=ctx, action=CATEGORY_RELEVANCE_CHANGED, dry_run=dry_run
        )


# --- Anonymous garage cookie (FIT.6) ------------------------------------------------------------


def anon_storage_enabled() -> bool:
    """Whether the garage may be written to the visitor's browser: the operator setting is
    `exempt` and the scheme does not hold sensitive items (then the garage lasts for the visit
    only, as for accounts)."""
    if getattr(settings, "GARAGE_ANON_STORAGE", "exempt") != "exempt":
        return False
    return not current_scheme().contains_special_category


def read_garage(request: HttpRequest) -> garage.Garage:
    """The garage in the request's cookie; empty when the storage is off. Nothing is logged or
    stored server-side."""
    if not anon_storage_enabled():
        return garage.EMPTY
    return validated_garage(garage.parse(request.COOKIES.get(garage.COOKIE_NAME)))


def validated_garage(parsed: garage.Garage) -> garage.Garage:
    """Every vehicle checked against the catalogue (node exists and is active, year in range);
    the labels kept are the catalogue's own. One miss makes the cookie invalid: an empty garage
    (spec 17 section 17.4.2). The views clear the cookie on the next garage action."""
    checked: list[garage.Vehicle] = []
    for vehicle in parsed.vehicles:
        try:
            checked.append(vehicle_from_labels(vehicle.make, vehicle.model, vehicle.year))
        except FitmentError:
            return garage.EMPTY
    if not checked:
        return garage.EMPTY
    return garage.Garage(tuple(checked), parsed.active)


def active_vehicle(request: HttpRequest) -> dict[str, str] | None:
    """The active vehicle as make, model and year labels, for the checkout to hand to
    `orders` as opaque JSON (FIT.17); None without one."""
    return read_garage(request).active_snapshot()


def vehicle_from_labels(make: object, model: object, year: object = "") -> garage.Vehicle:
    """A vehicle a visitor chose, checked against the scheme: the make is an active root node,
    the model an active child of it, the year a plain year in the scheme's range (or empty when
    the scheme uses no year). Raises `FitmentError`."""
    vehicle = garage.make_vehicle(make, model, year)
    if vehicle is None:
        raise FitmentError("The vehicle is not valid.")
    root = (
        FitmentNode.objects.pickable()
        .filter(depth=1, normalized_name=engine.normalize_name(vehicle.make))
        .first()
    )
    child = (
        root.get_children()
        .filter(status=NodeStatus.ACTIVE, normalized_name=engine.normalize_name(vehicle.model))
        .first()
        if root is not None
        else None
    )
    if root is None or child is None:
        raise FitmentError("The vehicle is not in the catalogue of vehicles.")
    scheme = current_scheme()
    label = ""
    if scheme.year_enabled and vehicle.year:
        latest = timezone.now().year + scheme.year_max_offset
        if not (
            vehicle.year.isascii()
            and vehicle.year.isdigit()
            and scheme.year_min <= int(vehicle.year) <= latest
        ):
            raise FitmentError("The year is outside the range of the scheme.")
        label = vehicle.year
    return garage.Vehicle(root.name, child.name, label)


# --- What a product fits (FIT.2) ------------------------------------------------------------------


@dataclass(frozen=True)
class RowInput:
    """One row a seller declares. A year range may be open at one end (the scheme's bound)."""

    node: FitmentNode
    year_from: int | None = None
    year_to: int | None = None
    is_exclusion: bool = False
    note: FitmentNote | None = None


def max_rows_per_product() -> int:
    return int(getattr(settings, "FITMENT_MAX_ROWS_PER_PRODUCT", engine.DEFAULT_MAX_ROWS))


def _year_bounds(scheme: FitmentScheme) -> tuple[int, int]:
    return scheme.year_min, timezone.now().year + scheme.year_max_offset


def _engine_row(row: RowInput) -> engine.Row:
    return engine.Row(row.node.path, row.is_exclusion, row.year_from, row.year_to)


def product_fitment_warnings(mode: str, rows: Iterable[RowInput]) -> list[str]:
    """What the rules only warn about, for the seller to read before confirming."""
    return engine.declaration_problems(mode, [_engine_row(row) for row in rows]).warnings


def _is_always_visible(product: Product, mode: str, relevant_ids: set[int]) -> bool:
    return mode == engine.UNIVERSAL or product.category_id not in relevant_ids


def _refresh_always_visible(*, categories: Iterable[Category]) -> None:
    """After the operator marks or unmarks categories: refresh the denormalised flag of the
    declarations of their products."""
    relevant = relevant_category_ids()
    for category in categories:
        states = ProductFitmentState.objects.filter(product__category=category)
        if category.pk in relevant:
            states.update(always_visible=False)
            states.filter(mode=engine.UNIVERSAL).update(always_visible=True)
        else:
            states.update(always_visible=True)


def _row_snapshot(
    node: FitmentNode,
    years: tuple[int | None, int | None],
    is_exclusion: bool,
    note: FitmentNote | None,
    source: str,
) -> dict[str, Any]:
    return {
        "node": str(node.public_id),
        "years": None if years == (None, None) else list(years),
        "exclusion": is_exclusion,
        "note": None if note is None else note.code,
        "source": source,
    }


def _normalized_years(row: RowInput, low: int, high: int) -> tuple[int | None, int | None]:
    if row.year_from is None and row.year_to is None:
        return (None, None)
    return (
        row.year_from if row.year_from is not None else low,
        row.year_to if row.year_to is not None else high,
    )


def _fitment_snapshot(product: Product, mode: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda r: (r["node"], str(r["years"]), r["exclusion"]))
    return {
        "vendor": str(product.vendor.public_id),
        "product": str(product.public_id),
        "mode": mode,
        "rows": ordered,
    }


_SYSTEM_SOURCES = {AuditLog.Source.COMMAND, AuditLog.Source.TASK, AuditLog.Source.SYSTEM}


def _check_actor(product: Product, ctx: AuditContext) -> None:
    """The actor is platform staff or a member of the product's vendor with the products right.

    A system run (command, task, feed refresh) carries no user and is allowed; a user context
    whose user is missing, or belongs to another vendor, is refused (spec 17 §17.2.4, FIT.2).
    """
    if ctx.actor_id is None:
        if ctx.source in _SYSTEM_SOURCES:
            return
        raise PermissionDenied("A user action needs a signed-in actor.")
    user = get_user_model().objects.filter(pk=ctx.actor_id).first()
    if user is None or not user.is_active:
        raise PermissionDenied("The actor does not exist or is inactive.")
    if is_platform_staff(user):
        return
    if not vendors_services.has_vendor_permission(user, product.vendor, "products"):
        raise PermissionDenied("The actor may not change this product's fitment.")


def set_product_fitment(
    product: Product,
    mode: str,
    rows: Iterable[RowInput],
    source: str,
    *,
    ctx: AuditContext,
    dry_run: bool = False,
) -> ChangeSet:
    """Replace what `product` declares it fits (spec 17 §17.2.3, §17.2.4).

    A replace by hand, CSV or reference data swaps every row; a `feed` refresh swaps only the
    rows that came from a feed, so a seller's own rows stay. The ChangeSet lists each row that
    is created, changed or deleted; one `AuditLog` entry records the whole replace, with every
    row before and after. Nothing is written when the declaration is unchanged. Refusals raise
    `FitmentError`, an actor outside the product's vendor `PermissionDenied`; warnings (an
    exclusion with nothing to exclude from) are `product_fitment_warnings`.
    """
    _check_actor(product, ctx)
    if source not in ProductFitment.Source.values:
        raise FitmentError(f"Unknown source {source!r}.")
    wanted = list(rows)
    with transaction.atomic():
        scheme = current_scheme()
        low, high = _year_bounds(scheme)
        existing_state = (
            ProductFitmentState.objects.select_for_update().filter(product=product).first()
        )
        existing = list(
            ProductFitment.objects.select_for_update(of=("self",))
            .filter(product=product)
            .select_related("node", "note")
        )
        replaced = [
            r for r in existing if source != ProductFitment.Source.FEED or r.source == source
        ]
        kept = [r for r in existing if r not in replaced]
        known_nodes = {r.node_id for r in existing}
        nodes = {
            n.pk: n
            for n in FitmentNode.objects.select_for_update().filter(
                pk__in={r.node.pk for r in wanted}
            )
        }
        resolved: list[tuple[RowInput, FitmentNode]] = []
        for row in wanted:
            node = nodes.get(row.node.pk)
            if node is None:
                raise FitmentError("A node of the declaration no longer exists.")
            if node.status != NodeStatus.ACTIVE and node.pk not in known_nodes:
                raise FitmentError(f"The node {node.name!r} is not active: choose an active node.")
            resolved.append(
                (RowInput(node, row.year_from, row.year_to, row.is_exclusion, row.note), node)
            )
        new_rows = [(r, _normalized_years(r, low, high)) for r, _ in resolved]
        checked = [
            engine.Row(r.node.path, r.is_exclusion, years[0], years[1]) for r, years in new_rows
        ] + [engine.Row(r.node_path, r.is_exclusion, r.year_from, r.year_to) for r in kept]
        problems = engine.declaration_problems(
            mode,
            checked,
            max_rows=max_rows_per_product(),
            year_min=low,
            year_max=high,
            years_enabled=scheme.year_enabled,
        )
        if problems.errors:
            raise FitmentError(" ".join(problems.errors))

        old_key = {(r.node_id, (r.year_from, r.year_to), r.is_exclusion): r for r in replaced}
        new_key = {(r.node.pk, years, r.is_exclusion): (r, years) for r, years in new_rows}
        changeset = ChangeSet()
        to_create: list[tuple[RowInput, tuple[int | None, int | None]]] = []
        to_update: list[tuple[ProductFitment, RowInput]] = []
        for key, (row, years) in new_key.items():
            snap = _row_snapshot(row.node, years, row.is_exclusion, row.note, source)
            label = "fitment.productfitment"
            old = old_key.get(key)
            if old is None:
                to_create.append((row, years))
                changeset.create(label, f"{product.public_id}:{row.node.public_id}", snap)
            elif old.note_id != (row.note.pk if row.note else None) or old.source != source:
                before = _row_snapshot(old.node, key[1], old.is_exclusion, old.note, old.source)
                to_update.append((old, row))
                changeset.update(label, f"{product.public_id}:{row.node.public_id}", before, snap)
        for key, old in old_key.items():
            if key not in new_key:
                before = _row_snapshot(old.node, key[1], old.is_exclusion, old.note, old.source)
                changeset.delete(
                    "fitment.productfitment", f"{product.public_id}:{old.node.public_id}", before
                )

        before_mode = existing_state.mode if existing_state else engine.UNDECLARED
        relevant_ids = relevant_category_ids()
        visible = _is_always_visible(product, mode, relevant_ids)
        if existing_state is None and mode == engine.UNDECLARED and not wanted:
            return changeset  # nothing declared, nothing to record
        if before_mode != mode:
            changeset.update(
                "fitment.productfitmentstate",
                str(product.public_id),
                {"mode": before_mode},
                {"mode": mode},
            )
        changed = not changeset.is_empty
        if dry_run:
            return changeset

        def snapshot_of(state_mode: str, items: list[dict[str, Any]]) -> dict[str, Any]:
            return _fitment_snapshot(product, state_mode, items)

        if not changed:
            if existing_state is not None and existing_state.always_visible != visible:
                existing_state.always_visible = visible
                existing_state.save(update_fields=["always_visible", "updated_at"])
            return changeset
        before_snapshot = snapshot_of(
            before_mode,
            [
                _row_snapshot(r.node, (r.year_from, r.year_to), r.is_exclusion, r.note, r.source)
                for r in existing
            ],
        )
        for key, old in old_key.items():
            if key not in new_key:
                old.delete()
        for old, row in to_update:
            old.note = row.note
            old.source = source
            old.save(update_fields=["note", "source", "updated_at"])
        ProductFitment.objects.bulk_create(
            [
                ProductFitment(
                    product=product,
                    node=row.node,
                    node_path=row.node.path,
                    years=None if years == (None, None) else NumericRange(years[0], years[1], "[]"),
                    is_exclusion=row.is_exclusion,
                    note=row.note,
                    source=source,
                )
                for row, years in to_create
            ]
        )
        state = existing_state or ProductFitmentState(product=product)
        state.mode = mode
        state.always_visible = visible
        state.rows_version += 1
        state.save()
        after_rows = [
            _row_snapshot(r.node, years, r.is_exclusion, r.note, source) for r, years in new_rows
        ] + [
            _row_snapshot(r.node, (r.year_from, r.year_to), r.is_exclusion, r.note, r.source)
            for r in kept
        ]
        record(
            ctx,
            PRODUCT_FITMENT_SET,
            "fitment.productfitmentstate",
            str(product.public_id),
            before_snapshot,
            snapshot_of(mode, after_rows),
        )
        return changeset
