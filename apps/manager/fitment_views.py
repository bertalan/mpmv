"""Manager platform space: the fitment scheme, its nodes and its categories (FIT.1 part 2, D-38).

Thin adapters over `apps.fitment.services`, which hold the rules (no reorder or removal of a level
that has nodes, name clashes, merge with references moved) and write the ChangeSet and the
AuditLog. The scheme and the categories are operator settings (Configuration and tax); the nodes
are the vocabulary (Catalog and moderation). A change of the scheme shows its dry-run ChangeSet
first and is written only on confirmation, behind the session level of `operator_config` (CORE.25).
"""

import uuid
from collections.abc import Callable, Sequence
from typing import Any, ClassVar

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Exists, Model, OuterRef, QuerySet
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from django.views import View
from django_stubs_ext import StrOrPromise

from apps.accounts.decorators import requires_level_method
from apps.catalog.models import Category
from apps.core import translations
from apps.core.audit import audit_context
from apps.core.changeset import ChangeSet
from apps.fitment import engine, references, services
from apps.fitment.models import (
    FitmentCategory,
    FitmentLevel,
    FitmentNode,
    FitmentNodeAlias,
    NodeStatus,
)
from apps.fitment.services import FitmentError

from .fitment_forms import (
    AddLevelForm,
    AliasForm,
    LevelLabelsForm,
    NodeForm,
    NodeNameForm,
    ReasonForm,
    SchemeForm,
    TranslatedNameForm,
)
from .lists import BulkAction, BulkRun, Column, ListFilter, ManagerListView
from .middleware import manager_context
from .navigation import platform_role, registry
from .views import ManagerView

SCHEME_SECTION = "platform.fitment_scheme"
CATEGORIES_SECTION = "platform.fitment_categories"
NODES_SECTION = "platform.fitment_nodes"

type Run = Callable[..., ChangeSet]  # `run(dry_run=...)`


def writer(section: str) -> Callable[[Any], bool]:
    """The matrix cell of `section` that lets the role write, not only read."""
    return platform_role(section, write=True)


class WriteGate(View):
    """A POST needs the write cell of the matrix row: a read-only role gets the same 404 as a
    role without the row, before any step-up prompt."""

    write_section: ClassVar[str]
    # CAT.30: a section a role can see but not change answers 403; the role without the row
    # still gets 404. Off by default, so the other sections keep their 404.
    refuse_read_only: ClassVar[bool] = False

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        if request.method == "POST":
            ctx = manager_context(request)
            if ctx is None or not writer(self.write_section)(ctx):
                section = registry.get(self.write_section)
                if self.refuse_read_only and ctx is not None and section and section.allows(ctx):
                    raise PermissionDenied("This section is read-only for your role.")
                raise Http404("No Manager page here.")
        return super().dispatch(request, *args, **kwargs)


def level_label(level: FitmentLevel) -> str:
    """The level's label in the source language, else its code (the prefetched translations)."""
    rows = {row.language: row.label for row in level.translations.all()}
    return rows.get(translations.source_language()) or next(iter(rows.values()), level.code)


def echo(request: HttpRequest) -> list[tuple[str, str]]:
    """The posted fields to carry through the preview page: everything but the CSRF token."""
    skip = {"csrfmiddlewaretoken", "confirm"}
    return [(key, v) for key, values in request.POST.lists() if key not in skip for v in values]


# --- The scheme (Configuration and tax)


class SchemeView(WriteGate, ManagerView):
    section = SCHEME_SECTION
    write_section = SCHEME_SECTION
    template_name = "manager/platform/fitment_scheme.html"
    preview_template_name = "manager/platform/fitment_preview.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        scheme = services.current_scheme()
        bound_target, bound_form = kwargs.get("bound_target"), kwargs.get("bound_form")
        levels = list(
            FitmentLevel.objects.order_by("position")
            .annotate(node_count=Count("nodes"))
            .prefetch_related("translations")
        )
        rows = []
        for level in levels:
            if bound_target == level.code and isinstance(bound_form, LevelLabelsForm):
                form = bound_form
            else:
                initial = {}
                for row in level.translations.all():
                    initial[f"label_{row.language}"] = row.label
                    initial[f"plural_{row.language}"] = row.plural_label
                    initial[f"placeholder_{row.language}"] = row.placeholder
                form = LevelLabelsForm(initial=initial, auto_id=f"rename-{level.code}-%s")
            rows.append({"level": level, "label": level_label(level), "form": form})
        context["scheme_form"] = (
            bound_form
            if bound_target == "scheme"
            else SchemeForm(
                initial={name: getattr(scheme, name) for name in services.SCHEME_FIELDS}
            )
        )
        context["add_form"] = (
            bound_form
            if bound_target == "add"
            else AddLevelForm(auto_id="add-level-%s")
            if len(levels) < engine.MAX_LEVELS
            else None
        )
        context.update(
            levels=rows,
            can_write=writer(SCHEME_SECTION)(self.manager),
            title=_("Fitment scheme"),
            max_levels=engine.MAX_LEVELS,
        )
        return context

    def level(self, request: HttpRequest) -> FitmentLevel:
        level = FitmentLevel.objects.filter(code=request.POST.get("level", "")).first()
        if level is None:
            raise Http404("No such level.")
        return level

    def build(self, request: HttpRequest, ctx: Any) -> tuple[str, Any, Run | None]:
        """Parse one action: (what the page binds on a refusal, the form, the service call)."""
        action = request.POST.get("action", "")
        if action == "save":
            form = SchemeForm(request.POST)
            if not form.is_valid():
                return "scheme", form, None
            values = dict(form.cleaned_data)
            return (
                "scheme",
                form,
                lambda dry_run: services.save_scheme(values, ctx=ctx, dry_run=dry_run),
            )
        if action == "add_level":
            add = AddLevelForm(request.POST, auto_id="add-level-%s")
            if not add.is_valid():
                return "add", add, None
            return (
                "add",
                add,
                lambda dry_run: (
                    services.add_level(
                        code=add.cleaned_data["code"],
                        labels=add.labels(),
                        translate_names=add.cleaned_data["translate_names"],
                        ctx=ctx,
                        dry_run=dry_run,
                    ).changes
                ),
            )
        if action == "rename_level":
            level = self.level(request)
            rename = LevelLabelsForm(request.POST, auto_id=f"rename-{level.code}-%s")
            if not rename.is_valid():
                return level.code, rename, None
            return (
                level.code,
                rename,
                lambda dry_run: services.rename_level(
                    level, labels=rename.labels(), ctx=ctx, dry_run=dry_run
                ),
            )
        if action == "remove_level":
            doomed = self.level(request)
            return "", None, lambda dry_run: services.remove_level(doomed, ctx=ctx, dry_run=dry_run)
        if action == "translate_names":
            target = self.level(request)
            value = request.POST.get("value") == "1"
            return (
                "",
                None,
                lambda dry_run: services.set_translate_names(
                    target, value, ctx=ctx, dry_run=dry_run
                ),
            )
        if action == "reorder":
            codes = list(FitmentLevel.objects.order_by("position").values_list("code", flat=True))
            try:
                order = sorted(codes, key=lambda code: int(request.POST[f"position-{code}"]))
            except (KeyError, ValueError):
                raise FitmentError("Give every level a position number.") from None
            return (
                "",
                None,
                lambda dry_run: services.reorder_levels(order, ctx=ctx, dry_run=dry_run),
            )
        raise Http404("No such action.")

    @requires_level_method("operator_config")  # group 2: a passkey once, or an open window
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        ctx = audit_context(request)
        confirmed = request.POST.get("confirm") == "1"
        try:
            target, form, run = self.build(request, ctx)
            if run is None:
                return self.render_to_response(
                    self.get_context_data(bound_target=target, bound_form=form)
                )
            changes = run(dry_run=not confirmed)
        except FitmentError as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(request.path)
        if confirmed:
            messages.success(request, _("The scheme is updated."))
            return HttpResponseRedirect(request.path)
        context = self.get_context_data()
        context.update(
            changes=changes.changes,
            echo=echo(request),
            title=_("Preview of the change"),
            cancel_url=request.path,
        )
        return self.response_class(
            request=request,
            template=[self.preview_template_name],
            context=context,
            using=self.template_engine,
        )


# --- Categories expected to declare fitment (Configuration and tax)


class RelevanceFilter(ListFilter):
    def apply[M: Model](self, rows: QuerySet[M], value: str) -> QuerySet[M]:
        if value in ("yes", "no"):
            return rows.filter(is_relevant=value == "yes")
        return rows


def _set_gate(value: bool) -> BulkRun:
    def run(objects: list[Category], *, ctx: Any, dry_run: bool) -> ChangeSet:
        return services.set_fitment_publish_gate(objects, value, ctx=ctx, dry_run=dry_run)

    return run


def _set_relevant(value: bool) -> BulkRun:
    def run(objects: list[Category], *, ctx: Any, dry_run: bool) -> ChangeSet:
        return services.set_fitment_relevant(objects, value, ctx=ctx, dry_run=dry_run)

    return run


class CategoryList(ManagerListView):
    """Every category with whether its items are expected to declare fitment (none at first)."""

    section = CATEGORIES_SECTION
    paginate_by = 50
    columns = (
        Column(
            gettext_lazy("Category"),
            lambda category: ("- " * (category.get_depth() - 1)) + str(category),
        ),
        Column(
            gettext_lazy("Expected to declare fitment"),
            lambda category: _("Yes") if category.is_relevant else _("No"),
        ),
        Column(
            gettext_lazy("Declaration required to publish"),
            lambda category: _("Yes") if category.gate_on else _("No"),
        ),
    )
    filters = (
        RelevanceFilter(
            "relevant",
            gettext_lazy("Fitment"),
            (("yes", gettext_lazy("Expected")), ("no", gettext_lazy("Not expected"))),
            lookup="is_relevant",
        ),
    )
    bulk_actions = (
        BulkAction(
            "relevant",
            gettext_lazy("Expect fitment"),
            _set_relevant(True),
            allowed=writer(CATEGORIES_SECTION),
        ),
        BulkAction(
            "not_relevant",
            gettext_lazy("Do not expect fitment"),
            _set_relevant(False),
            allowed=writer(CATEGORIES_SECTION),
        ),
        BulkAction(
            "require_declaration",
            gettext_lazy("Require a declaration to publish"),
            _set_gate(True),
            allowed=writer(CATEGORIES_SECTION),
        ),
        BulkAction(
            "no_declaration",
            gettext_lazy("Do not require a declaration to publish"),
            _set_gate(False),
            allowed=writer(CATEGORIES_SECTION),
        ),
    )

    def get_queryset(self) -> QuerySet[Category]:
        return (
            Category.objects.prefetch_related("translations")
            .annotate(
                is_relevant=Exists(FitmentCategory.objects.filter(category=OuterRef("pk"))),
                gate_on=Exists(
                    FitmentCategory.objects.filter(
                        category=OuterRef("pk"), require_declaration=True
                    )
                ),
            )
            .order_by("path")
        )

    def rows(self) -> QuerySet[Category]:
        # Categories are platform data, not vendor-owned: no vendor scoping applies.
        return self.get_queryset()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["title"] = _("Categories expected to declare fitment")
        return context


# --- Nodes (Catalog and moderation)


def _each(service: Callable[..., ChangeSet]) -> BulkRun:
    """Run `service` on every selected node, parents first, in one transaction. A dry run does
    the work for real and rolls it back, so a child approved with its parent previews correctly."""

    def run(objects: list[FitmentNode], *, ctx: Any, dry_run: bool) -> ChangeSet:
        result = ChangeSet()
        with transaction.atomic():
            for node in sorted(objects, key=lambda n: n.path):
                result.changes.extend(service(node, ctx=ctx).changes)
            if dry_run:
                transaction.set_rollback(True)
        return result

    return run


def _name_cell(node: FitmentNode) -> Any:
    url = reverse("manager:platform:fitment_node", args=[node.public_id])
    return format_html('<a href="{}">{}</a>', url, node.name)


def _parent_name(node: FitmentNode) -> str:
    parent = node.get_parent()
    return parent.name if parent else "—"


class NodeList(ManagerListView):
    section = NODES_SECTION
    template_name = "manager/platform/fitment_list.html"
    columns = (
        Column(gettext_lazy("Name"), _name_cell),
        Column(gettext_lazy("Level"), lambda node: level_label(node.level)),
        Column(gettext_lazy("Under"), _parent_name),
        Column(gettext_lazy("Status"), lambda node: node.get_status_display()),
        Column(
            gettext_lazy("Used by"), lambda node: references.reference_count(node), numeric=True
        ),
    )
    filters: ClassVar[Sequence[ListFilter]] = (
        ListFilter(
            "status",
            gettext_lazy("Status"),
            tuple((str(value), str(label)) for value, label in NodeStatus.choices),
            lookup="status",
        ),
        ListFilter(
            "level",
            gettext_lazy("Level position"),
            tuple((str(n), str(n)) for n in range(1, engine.MAX_LEVELS + 1)),
            lookup="level__position",
        ),
    )
    bulk_actions: ClassVar[Sequence[BulkAction]] = (
        BulkAction(
            "approve",
            gettext_lazy("Approve"),
            _each(lambda node, ctx: services.approve_node(node, ctx=ctx)),
            allowed=writer(NODES_SECTION),
        ),
        BulkAction(
            "retire",
            gettext_lazy("Retire"),
            _each(lambda node, ctx: services.retire_node(node, ctx=ctx)),
            allowed=writer(NODES_SECTION),
        ),
    )
    bulk_errors = (FitmentError,)
    heading: StrOrPromise = gettext_lazy("Fitment nodes")

    def get_queryset(self) -> QuerySet[FitmentNode]:
        rows: QuerySet[FitmentNode] = FitmentNode.objects.select_related("level").prefetch_related(
            "level__translations"
        )
        return rows

    def rows(self) -> QuerySet[FitmentNode]:
        # The vocabulary is platform data, not vendor-owned: no vendor scoping applies.
        return self.get_queryset()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["title"] = self.heading
        context["links"] = [
            (_("Proposals"), reverse("manager:platform:fitment_proposals")),
            (_("All nodes"), reverse("manager:platform:fitment_nodes")),
            (_("New node"), reverse("manager:platform:fitment_node_new")),
        ]
        return context


class ProposalList(NodeList):
    """The review queue: nodes proposed by sellers or staff, invisible to pickers until approved."""

    filters = ()
    bulk_actions = NodeList.bulk_actions[:1]
    heading = gettext_lazy("Proposed nodes")

    def get_queryset(self) -> QuerySet[FitmentNode]:
        return super().get_queryset().filter(status=NodeStatus.PROPOSED)


class NodeCreateView(WriteGate, ManagerView):
    section = NODES_SECTION
    write_section = NODES_SECTION
    template_name = "manager/platform/fitment_node_form.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = kwargs.get("form")
        if form is None:
            form = NodeForm(initial={"parent": self.request.GET.get("parent", "")})
        parent = None
        value = form["parent"].value()
        if value:
            parent = FitmentNode.objects.filter(public_id=_uuid(value)).first()
        context.update(form=form, parent=parent, title=_("New fitment node"))
        return context

    @requires_level_method("catalogue_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        form = NodeForm(request.POST)
        if form.is_valid():
            try:
                applied = services.create_node(
                    name=form.cleaned_data["name"],
                    parent=form.cleaned_data["parent"],
                    status=form.cleaned_data["status"],
                    ctx=audit_context(request),
                )
            except FitmentError as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("The node is added."))
                assert applied.obj is not None  # noqa: S101  # applied for real
                return HttpResponseRedirect(
                    reverse("manager:platform:fitment_node", args=[applied.obj.public_id])
                )
        return self.render_to_response(self.get_context_data(form=form))


def _uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def get_node(public_id: uuid.UUID) -> FitmentNode:
    node: FitmentNode | None = (
        FitmentNode.objects.select_related("level").filter(public_id=public_id).first()
    )
    if node is None:
        raise Http404("No such node.")
    return node


class NodeDetailView(WriteGate, ManagerView):
    section = NODES_SECTION
    write_section = NODES_SECTION
    template_name = "manager/platform/fitment_node.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        node = get_node(kwargs["public_id"])
        names = {row.language: row.name for row in node.translations.all()}
        context.update(
            node=node,
            level=level_label(node.level),
            ancestors=list(node.get_ancestors()),
            children=list(node.get_children()),
            aliases=list(node.aliases.order_by("alias")),
            names=[
                (code, str(label), names.get(code, ""))
                for code, label in _languages()
                if node.level.translate_names
            ],
            used_by=references.reference_counts(node),
            can_write=writer(NODES_SECTION)(self.manager),
            rename_form=kwargs.get("rename_form")
            or NodeNameForm(initial={"name": node.name}, auto_id="rename-%s"),
            alias_form=kwargs.get("alias_form") or AliasForm(auto_id="alias-%s"),
            name_form=kwargs.get("name_form"),
            refuse_form=kwargs.get("refuse_form") or ReasonForm(auto_id="refuse-%s"),
            title=node.name,
        )
        return context

    @requires_level_method("catalogue_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        node = get_node(kwargs["public_id"])
        action = request.POST.get("action", "")
        ctx = audit_context(request)
        here = reverse("manager:platform:fitment_node", args=[node.public_id])
        back = here
        try:
            if action == "rename":
                form = NodeNameForm(request.POST, auto_id="rename-%s")
                if not form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(rename_form=form, **kwargs)
                    )
                services.rename_node(node, form.cleaned_data["name"], ctx=ctx)
            elif action == "add_alias":
                alias_form = AliasForm(request.POST, auto_id="alias-%s")
                if not alias_form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(alias_form=alias_form, **kwargs)
                    )
                services.add_alias(
                    node,
                    alias_form.cleaned_data["alias"],
                    language=alias_form.cleaned_data["language"] or None,
                    ctx=ctx,
                )
            elif action == "remove_alias":
                alias = FitmentNodeAlias.objects.filter(
                    node=node, public_id=_uuid(request.POST.get("alias", "")) or uuid.UUID(int=0)
                ).first()
                if alias is None:
                    raise Http404("No such alias.")
                services.remove_alias(alias, ctx=ctx)
            elif action == "set_name":
                name_form = TranslatedNameForm(request.POST)
                if not name_form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(name_form=name_form, **kwargs)
                    )
                services.set_node_name(
                    node,
                    name_form.cleaned_data["language"],
                    name_form.cleaned_data["name"],
                    ctx=ctx,
                )
            elif action == "approve":
                services.approve_node(node, ctx=ctx)
            elif action == "refuse":
                refuse_form = ReasonForm(request.POST, auto_id="refuse-%s")
                if not refuse_form.is_valid():
                    return self.render_to_response(
                        self.get_context_data(refuse_form=refuse_form, **kwargs)
                    )
                services.refuse_node(node, reason=refuse_form.cleaned_data["reason"], ctx=ctx)
                back = reverse("manager:platform:fitment_proposals")
            elif action == "retire":
                services.retire_node(node, ctx=ctx)
            elif action == "reactivate":
                services.reactivate_node(node, ctx=ctx)
            elif action == "delete":
                services.delete_node(node, ctx=ctx)
                back = reverse("manager:platform:fitment_nodes")
            else:
                raise Http404("No such action.")
        except FitmentError as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(here)
        messages.success(request, _("Saved."))
        return HttpResponseRedirect(back)


def _languages() -> list[tuple[str, Any]]:
    return list(settings.LANGUAGES)


class NodeMergeView(WriteGate, ManagerView):
    """Merge a node into a sibling: its children, aliases and every reference move to the target,
    and the old id keeps answering as the target. Shown as a dry run first."""

    section = NODES_SECTION
    write_section = NODES_SECTION
    template_name = "manager/platform/fitment_merge.html"
    preview_template_name = "manager/platform/fitment_preview.html"

    def candidates(self, node: FitmentNode) -> list[FitmentNode]:
        return [
            sibling
            for sibling in node.get_siblings()
            if sibling.pk != node.pk and sibling.status == NodeStatus.ACTIVE
        ]

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        node = get_node(kwargs["public_id"])
        context.update(
            node=node,
            candidates=self.candidates(node),
            children=node.get_children_count(),
            used_by=references.reference_counts(node),
            error=kwargs.get("error", ""),
            can_write=writer(NODES_SECTION)(self.manager),
            title=_("Merge %(name)s") % {"name": node.name},
        )
        return context

    @requires_level_method("bulk_edit")
    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        node = get_node(kwargs["public_id"])
        target = next(
            (c for c in self.candidates(node) if str(c.public_id) == request.POST.get("target")),
            None,
        )
        if target is None:
            return self.render_to_response(
                self.get_context_data(error=_("Choose the node to merge into."), **kwargs)
            )
        confirmed = request.POST.get("confirm") == "1"
        here = reverse("manager:platform:fitment_node_merge", args=[node.public_id])
        try:
            changes = services.merge_nodes(
                node, target, ctx=audit_context(request), dry_run=not confirmed
            )
        except FitmentError as error:
            messages.error(request, str(error))
            return HttpResponseRedirect(here)
        if confirmed:
            messages.success(request, _("The nodes are merged."))
            return HttpResponseRedirect(
                reverse("manager:platform:fitment_node", args=[target.public_id])
            )
        context = self.get_context_data(**kwargs)
        context.update(
            changes=changes.changes,
            echo=echo(request),
            title=_("Preview of the merge"),
            cancel_url=here,
        )
        return self.response_class(
            request=request,
            template=[self.preview_template_name],
            context=context,
            using=self.template_engine,
        )
