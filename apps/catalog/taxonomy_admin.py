"""Staff admin for the category tree, taxonomies, terms and brands, with their texts in every
language (CAT.2). Imported by `admin.py`.

Tree nodes are placed with treebeard's form fields ("Position" and "Relative to"). Placing a node
writes to the tree before the inlines are validated, so the whole request is rolled back when
anything on the page is invalid (as treebeard's own TreeAdmin does). Deleting a node with
children is refused, and so is the bulk delete action: treebeard would delete the whole subtree
without listing it on the confirmation page.

Terms can be many thousands (every maker and model of a compatibility taxonomy), so the forms never
list them all: a term's place ("Relative to") and a product's terms are looked up by autocomplete,
which searches term names, slugs and taxonomy codes, and a term's place among the terms of its own
taxonomy only.
"""

from collections.abc import Callable
from typing import Any, cast

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _
from treebeard.forms import MoveNodeForm, TreeNodeChoiceField

from apps.core.audit import audit_context
from apps.core.models import ImageLicence, MediaImage
from apps.core.text import ascii_int
from apps.vendors import engine as vendors_engine

from . import brands
from .models import Product
from .taxonomy import (
    Brand,
    BrandBatch,
    BrandTranslation,
    Category,
    CategoryTranslation,
    Manufacturer,
    Taxonomy,
    TaxonomyTranslation,
    Term,
    TermTranslation,
    brand_logo_images,
)

TIMESTAMPS = ("public_id", "created_at", "updated_at")
TREE_FIELDS = ("treebeard_position", "treebeard_ref_node")
SEO_TEXTS = ("description", "seo_title", "seo_description")
# Query parameter of the admin autocomplete view that narrows term results to one taxonomy.
TAXONOMY_PARAM = "taxonomy"


class TermReferenceWidget(AutocompleteSelect):
    """A term looked up by the admin autocomplete, narrowed to `taxonomy` when it is known.

    Django's autocomplete view needs a model field pointing to the terms to authorize and run the
    search: `Product.terms` is that field (it has no `limit_choices_to`), and `TermAdmin` reads
    the taxonomy from the URL.
    """

    def __init__(self, taxonomy: int | None) -> None:
        super().__init__(Product._meta.get_field("terms"), admin.site)
        self.taxonomy = taxonomy

    def get_url(self) -> str:
        url = super().get_url()
        if self.taxonomy is None:
            return url
        return f"{url}?{urlencode({TAXONOMY_PARAM: self.taxonomy})}"


class TreeNodeForm(MoveNodeForm):  # type: ignore[misc]  # treebeard is untyped
    """Treebeard's move form, adding a new node directly at its place.

    Treebeard's own form first adds a new node as the last child of the reference node and then
    moves it, so for a moment a term "after X" is a child of X, which a flat taxonomy refuses.
    """

    treebeard_position = forms.ChoiceField(
        required=True,
        label=_("Position"),
        help_text=_(
            "Where to place this item next to the one chosen below: first inside it, before it "
            "or after it. Required."
        ),
    )
    treebeard_ref_node = TreeNodeChoiceField(
        required=False,
        label=_("Relative to"),
        empty_label=_("-- root --"),
        queryset=None,  # populated in __init__
        help_text=_(
            "The item to place this one next to. Leave it empty to put this one at the top "
            "level of the list."
        ),
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["treebeard_ref_node"]
        field.queryset = field.queryset.prefetch_related("translations")  # the choice labels

    def target_parent(self) -> Any:
        """The parent the node will have once saved (None: a root), from the cleaned data."""
        reference = self.cleaned_data.get("treebeard_ref_node")
        if reference is None:
            return None
        if self.cleaned_data.get("treebeard_position") == "first-child":
            return reference
        return reference.get_parent()

    def save(self, commit: bool = True) -> Any:
        if not self.instance._state.adding:
            return super().save(commit=commit)
        reference = self.cleaned_data.pop("treebeard_ref_node", None)
        position = self.cleaned_data.pop("treebeard_position")
        model = self._meta.model
        if reference is None:
            model.add_root(instance=self.instance)
        elif position == "first-child":
            first = reference.get_first_child()
            if first is None:
                reference.add_child(instance=self.instance)
            else:
                first.add_sibling("left", instance=self.instance)
        else:
            reference.add_sibling(position, instance=self.instance)
        return forms.ModelForm.save(self, commit=commit)


class TermForm(TreeNodeForm):
    """The root terms of every taxonomy are siblings in one forest, so a term's place is read and
    set among the roots of its own taxonomy: "-- root --" means its first root, not the forest's.
    """

    class Meta:
        model = Term
        fields = ("taxonomy", "attributes")

    treebeard_ref_node = TreeNodeChoiceField(
        required=False,
        label=_("Relative to"),
        empty_label=_("-- root --"),
        queryset=None,  # populated in __init__
        help_text=_(
            "The category or tag to place this one next to. Leave it empty to put this one at "
            "the top level of its set."
        ),
    )

    def _get_initial(self, instance: Term) -> dict[str, Any]:
        if instance._state.adding or not instance.is_root():
            initial: dict[str, Any] = super()._get_initial(instance)
            return initial  # a child's siblings and parent are in its taxonomy
        previous = self._taxonomy_roots(instance).filter(path__lt=instance.path).last()
        if previous is None:
            return {"treebeard_ref_node": None, "treebeard_position": "first-child"}
        return {"treebeard_ref_node": previous, "treebeard_position": "right"}

    @staticmethod
    def _taxonomy_roots(term: Term) -> "models.QuerySet[Term]":
        roots: models.QuerySet[Term] = Term.get_root_nodes()  # treebeard is untyped
        return roots.filter(taxonomy_id=term.taxonomy_id)

    def save(self, commit: bool = True) -> Any:
        moved = set(self.changed_data) & set(TREE_FIELDS)
        if self.instance._state.adding or not moved or self.cleaned_data.get("treebeard_ref_node"):
            return super().save(commit=commit)
        # To the front of its taxonomy (treebeard would move it to the front of the forest).
        del self.cleaned_data["treebeard_ref_node"], self.cleaned_data["treebeard_position"]
        self.instance.save()
        first = self._taxonomy_roots(self.instance).first()
        if first is not None and first.pk != self.instance.pk:
            self.instance.move(first, "left")
            self.instance.refresh_from_db()
        return forms.ModelForm.save(self, commit=commit)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = self.fields["treebeard_ref_node"]
        field.widget = TermReferenceWidget(self._known_taxonomy())
        field.widget.is_required = field.required
        # Setting the queryset also gives the new widget its choices.
        field.queryset = field.queryset.select_related("taxonomy")
        if self.instance.taxonomy_id is not None:
            # An existing term moves only within its taxonomy.
            field.queryset = field.queryset.filter(taxonomy_id=self.instance.taxonomy_id)

    def _known_taxonomy(self) -> int | None:
        """The taxonomy the term belongs to, or the one chosen (posted, or given in the URL)."""
        if self.instance.taxonomy_id is not None:
            return int(self.instance.taxonomy_id)
        chosen = self.data.get("taxonomy") if self.is_bound else self.initial.get("taxonomy")
        chosen = str(getattr(chosen, "pk", chosen) or "")
        return ascii_int(chosen)

    def clean(self) -> dict[str, Any]:
        cleaned: dict[str, Any] = super().clean()
        if "taxonomy" in self.fields:
            self.instance.taxonomy = cleaned.get("taxonomy")
        if self.instance.taxonomy_id is None or "treebeard_position" not in cleaned:
            return cleaned
        error = self.instance.placement_error(self.target_parent())
        if error:
            self.add_error("treebeard_ref_node", error)
        return cleaned


class TreeNodeAdmin[N: models.Model](admin.ModelAdmin[N]):
    ordering = ("path",)

    def _changeform_view(self, request: HttpRequest, *args: Any, **kwargs: Any) -> Any:
        # Django's private hook, as overridden by treebeard's TreeAdmin (missing from the stubs).
        response = super()._changeform_view(request, *args, **kwargs)  # type: ignore[misc]
        if getattr(response, "context_data", {}).get("errors"):
            transaction.set_rollback(True)  # the node was already placed: undo it
        return response

    def has_delete_permission(self, request: HttpRequest, obj: N | None = None) -> bool:
        children: int = getattr(obj, "numchild", 0)
        if children:
            return False  # move or delete its children first
        return super().has_delete_permission(request, obj)

    def get_actions(
        self,
        request: HttpRequest,
        action_location: admin.ActionLocation = admin.ActionLocation.CHANGE_LIST,
    ) -> dict[str, Any]:
        actions = super().get_actions(request, action_location)
        actions.pop("delete_selected", None)
        return actions

    @admin.display(description="name")
    def indented_name(self, obj: N) -> str:
        depth = int(getattr(obj, "depth", 1) or 1)
        return format_html("{}{}", "— " * (depth - 1), str(obj))

    @admin.display(description="children")
    def children(self, obj: N) -> int:
        count: int = getattr(obj, "numchild", 0)
        return count


class CategoryTranslationInline(admin.StackedInline[CategoryTranslation, Category]):
    model = CategoryTranslation
    extra = 1
    fields = ("language", "status", "name", "slug", *SEO_TEXTS)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Category)
class CategoryAdmin(TreeNodeAdmin[Category]):
    form = TreeNodeForm
    list_display = ("indented_name", "languages", "children")
    search_fields = ("translations__name", "translations__slug")
    readonly_fields = TIMESTAMPS
    fields = (*TREE_FIELDS, *TIMESTAMPS)
    inlines = (CategoryTranslationInline,)

    def get_queryset(self, request: HttpRequest) -> "models.QuerySet[Category]":
        return super().get_queryset(request).prefetch_related("translations")

    @admin.display(description="languages")
    def languages(self, obj: Category) -> str:
        return ", ".join(row.language for row in obj.translations.all())


class TaxonomyTranslationInline(admin.TabularInline[TaxonomyTranslation, Taxonomy]):
    model = TaxonomyTranslation
    extra = 1
    fields = ("language", "status", "name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Taxonomy)
class TaxonomyAdmin(admin.ModelAdmin[Taxonomy]):
    list_display = ("code", "hierarchical", "filterable", "has_landing")
    list_filter = ("hierarchical", "filterable", "has_landing")
    search_fields = ("code", "translations__name")
    readonly_fields = TIMESTAMPS
    fields = ("code", "hierarchical", "filterable", "has_landing", "term_attributes", *TIMESTAMPS)
    inlines = (TaxonomyTranslationInline,)


class TermTranslationInline(admin.StackedInline[TermTranslation, Term]):
    model = TermTranslation
    extra = 1
    fields = ("language", "status", "name", "slug", *SEO_TEXTS)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Term)
class TermAdmin(TreeNodeAdmin[Term]):
    form = TermForm
    list_display = ("indented_name", "taxonomy", "children")
    list_filter = ("taxonomy",)
    list_select_related = ("taxonomy",)
    search_fields = ("translations__name", "translations__slug", "taxonomy__code")
    fields = ("taxonomy", "attributes", *TREE_FIELDS, *TIMESTAMPS)
    inlines = (TermTranslationInline,)

    def get_queryset(self, request: HttpRequest) -> "models.QuerySet[Term]":
        return super().get_queryset(request).prefetch_related("translations")

    def get_search_results(
        self, request: HttpRequest, queryset: "models.QuerySet[Term]", search_term: str
    ) -> tuple["models.QuerySet[Term]", bool]:
        """The admin search; an autocomplete lookup may also name a taxonomy (TAXONOMY_PARAM)."""
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        match = request.resolver_match
        taxonomy = request.GET.get(TAXONOMY_PARAM)
        if match is None or match.url_name != "autocomplete" or taxonomy is None:
            return queryset, may_have_duplicates
        taxonomy_id = ascii_int(taxonomy)
        if taxonomy_id is None:
            return queryset.none(), may_have_duplicates
        return queryset.filter(taxonomy_id=taxonomy_id), may_have_duplicates

    def get_readonly_fields(self, request: HttpRequest, obj: Term | None = None) -> Any:
        # A term changes taxonomy only by being recreated: its children would be left behind.
        return (*TIMESTAMPS, "taxonomy") if obj is not None else TIMESTAMPS


class BrandTranslationInline(admin.StackedInline[BrandTranslation, Brand]):
    model = BrandTranslation
    extra = 1
    fields = ("language", "status", "slug", *SEO_TEXTS)


class BrandForm(forms.ModelForm[Brand]):
    """A brand staff create is approved unless they say otherwise."""

    class Meta:
        model = Brand
        fields = (
            "name",
            "status",
            "wikidata_id",
            "url",
            "same_as",
            "logo",
            "logo_alt",
            "manufacturer",
        )

    # A new logo file, with the licence fields of CMS.8 (the SEC.2 checks run in `clean`).
    logo_file = forms.FileField(
        required=False,
        label=_("New logo file"),
        help_text=_(
            "Upload a logo to replace the current one: PNG, JPEG, WebP or SVG, checked and "
            "re-encoded without metadata. It is shown on the brand page and in its structured "
            "data. Example: the brand's official logo."
        ),
    )
    logo_licence = forms.ModelChoiceField(
        queryset=ImageLicence.objects.all(),
        required=False,
        label=_("Logo licence"),
        help_text=_(
            "The licence the logo is used under; only licences that allow commercial use are "
            "listed. Needed with a new file. Example: 'Own work' for a logo the brand gave us."
        ),
    )
    logo_author = forms.CharField(
        required=False,
        max_length=200,
        label=_("Logo author or rights holder"),
        help_text=_(
            "Who to credit for the logo. Needed with a new file. Example: the brand's name."
        ),
    )
    logo_source_url = forms.URLField(
        required=False,
        max_length=500,
        label=_("Logo source URL"),
        help_text=_(
            "Where the logo comes from; some licences require it. Example: the brand's press page."
        ),
    )
    logo_credit_text = forms.CharField(
        required=False,
        max_length=250,
        label=_("Logo credit line"),
        help_text=_(
            "Optional text that replaces the generated credit line. Example: 'Logo: Acme Ltd'."
        ),
    )
    logo_permission_ref = forms.CharField(
        required=False,
        max_length=200,
        label=_("Logo permission reference"),
        help_text=_(
            "Reference of the stored written permission, when the licence needs one. "
            "Example: 'permission-2026-11-acme.pdf'."
        ),
    )

    # Which field of the form shows each error of `MediaImage.full_clean`.
    LOGO_ERROR_FIELDS = {
        "licence": "logo_licence",
        "author": "logo_author",
        "source_url": "logo_source_url",
        "credit_text": "logo_credit_text",
        "permission_ref": "logo_permission_ref",
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Read-only on an existing brand (BrandAdmin), so the field may be absent.
        if "status" in self.fields:
            self.fields["status"].required = False
        # Only the platform's logos collection (a vendor's or a page's image is not offered).
        cast(
            "forms.ModelChoiceField[MediaImage]", self.fields["logo"]
        ).queryset = brand_logo_images()

    def clean_status(self) -> str:
        return str(self.cleaned_data.get("status") or Brand.Status.APPROVED)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        upload = data.get("logo_file")
        if upload is None:
            return data
        problem = vendors_engine.logo_alt_errors(str(data.get("logo_alt") or ""))
        if problem:
            self.add_error("logo_alt", problem)
        try:
            brands.build_logo_image(
                str(data.get("name") or "Brand"),
                upload,
                licence=data.get("logo_licence"),
                author=str(data.get("logo_author") or ""),
                source_url=str(data.get("logo_source_url") or ""),
                credit_text=str(data.get("logo_credit_text") or ""),
                permission_ref=str(data.get("logo_permission_ref") or ""),
            )
        except ValidationError as error:
            for name, messages_ in error.message_dict.items():
                for message in messages_:
                    self.add_error(self.LOGO_ERROR_FIELDS.get(name, "logo_file"), message)
        finally:
            upload.seek(0)  # the service reads the file again
        return data


class BrandMergeForm(forms.Form):
    """The brand the selected ones are merged into."""

    target = forms.ModelChoiceField(
        queryset=Brand.objects.none(),
        widget=AutocompleteSelect(Product._meta.get_field("brand"), admin.site),
        label=_("Merge into"),
        help_text=_(
            "The approved brand that keeps the products and takes over the old page address. "
            "Example: merge the proposal 'Acmee' into the existing brand 'Acme'."
        ),
    )

    def __init__(self, *args: Any, exclude: list[int], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        field = cast("forms.ModelChoiceField[Brand]", self.fields["target"])
        field.queryset = Brand.objects.filter(status=Brand.Status.APPROVED).exclude(pk__in=exclude)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin[Brand]):
    form = BrandForm
    list_display = ("name", "status", "proposed_by", "batch", "manufacturer", "wikidata_id")
    list_filter = ("status",)
    search_fields = ("name", "wikidata_id")
    readonly_fields = (*TIMESTAMPS, "proposed_by", "batch")
    list_select_related = ("proposed_by", "batch", "manufacturer")
    fields = (
        "name",
        "status",
        "wikidata_id",
        "url",
        "same_as",
        "logo",
        "logo_alt",
        "logo_file",
        "logo_licence",
        "logo_author",
        "logo_source_url",
        "logo_credit_text",
        "logo_permission_ref",
        "manufacturer",
        "proposed_by",
        "batch",
        *TIMESTAMPS,
    )
    autocomplete_fields = ("manufacturer",)
    inlines = (BrandTranslationInline,)
    actions = ("approve_selected", "reject_selected", "merge_selected")

    def get_readonly_fields(self, request: HttpRequest, obj: Brand | None = None) -> Any:
        """The status of an existing brand changes only through the approve, reject and merge
        actions: they release products, notify the vendor and write the right audit entry."""
        if obj is not None:
            return (*self.readonly_fields, "status")
        return self.readonly_fields

    def has_merge_permission(self, request: HttpRequest) -> bool:
        """A merge deletes brands and rewrites redirects: it needs both permissions (Django
        offers an action when ANY of its listed permissions is held, so this checks both)."""
        return self.has_change_permission(request) and self.has_delete_permission(request)

    def save_model(
        self, request: HttpRequest, obj: Brand, form: forms.ModelForm[Brand], change: bool
    ) -> None:
        ctx = audit_context(request)
        data = form.cleaned_data
        # One transaction: a logo that fails leaves the brand as it was.
        with transaction.atomic():
            brands.save_brand(obj, ctx=ctx)
            if data.get("logo_file") is not None:
                brands.set_brand_logo(
                    obj,
                    upload=data["logo_file"],
                    alt=str(obj.logo_alt),
                    licence=data.get("logo_licence"),
                    author=str(data.get("logo_author") or ""),
                    source_url=str(data.get("logo_source_url") or ""),
                    credit_text=str(data.get("logo_credit_text") or ""),
                    permission_ref=str(data.get("logo_permission_ref") or ""),
                    ctx=ctx,
                )

    def delete_model(self, request: HttpRequest, obj: Brand) -> None:
        brands.delete_brand(obj, ctx=audit_context(request))

    def delete_queryset(self, request: HttpRequest, queryset: "models.QuerySet[Brand]") -> None:
        ctx = audit_context(request)
        for brand in queryset:
            brands.delete_brand(brand, ctx=ctx)

    def _each(
        self,
        request: HttpRequest,
        queryset: "models.QuerySet[Brand]",
        step: Callable[..., Any],
        done: str,
    ) -> None:
        ctx = audit_context(request)
        count = 0
        for brand in queryset:
            try:
                step(brand, ctx=ctx)
            except ValidationError as error:
                self.message_user(request, f"{brand}: {' '.join(error.messages)}", messages.ERROR)
            else:
                count += 1
        if count:
            self.message_user(request, done.format(count=count), messages.SUCCESS)

    @admin.action(description=_("Approve the selected proposals"), permissions=("change",))
    def approve_selected(self, request: HttpRequest, queryset: "models.QuerySet[Brand]") -> None:
        self._each(request, queryset, brands.approve_brand, "Approved {count} brand(s).")

    @admin.action(description=_("Reject the selected proposals"), permissions=("change",))
    def reject_selected(self, request: HttpRequest, queryset: "models.QuerySet[Brand]") -> None:
        self._each(request, queryset, brands.reject_brand, "Rejected {count} brand(s).")

    @admin.action(
        description=_("Merge the selected brands into another brand"),
        permissions=("merge",),
    )
    def merge_selected(
        self, request: HttpRequest, queryset: "models.QuerySet[Brand]"
    ) -> HttpResponse | None:
        chosen = list(queryset)
        confirmed = "confirm" in request.POST
        form = BrandMergeForm(
            request.POST if confirmed else None, exclude=[brand.pk for brand in chosen]
        )
        if confirmed and form.is_valid():
            target = form.cleaned_data["target"]
            self._each(
                request,
                queryset,
                lambda brand, ctx: brands.merge_brands(brand, target, ctx=ctx),
                f"Merged {{count}} brand(s) into {target}.",
            )
            return None
        context = {
            **self.admin_site.each_context(request),
            "title": _("Merge brands"),
            "opts": self.model._meta,
            "form": form,
            "brands": chosen,
            "action": "merge_selected",
            "selected": [brand.pk for brand in chosen],
        }
        return TemplateResponse(request, "admin/catalog/brand/merge.html", context)


@admin.register(BrandBatch)
class BrandBatchAdmin(admin.ModelAdmin[BrandBatch]):
    """Import runs whose new brands wait for review: approved or rejected together."""

    list_display = ("source", "vendor", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("source",)
    list_select_related = ("vendor",)
    actions = ("approve_selected", "reject_selected")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: BrandBatch | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: BrandBatch | None = None) -> bool:
        return False

    def _each(
        self,
        request: HttpRequest,
        queryset: "models.QuerySet[BrandBatch]",
        step: Callable[..., Any],
        done: str,
    ) -> None:
        ctx = audit_context(request)
        count = 0
        for batch in queryset:
            try:
                step(batch, ctx=ctx)
            except ValidationError as error:
                self.message_user(request, f"{batch}: {' '.join(error.messages)}", messages.ERROR)
            else:
                count += 1
        if count:
            self.message_user(request, done.format(count=count), messages.SUCCESS)

    @admin.action(
        description=_("Approve all brands of the selected batches"), permissions=("review",)
    )
    def approve_selected(
        self, request: HttpRequest, queryset: "models.QuerySet[BrandBatch]"
    ) -> None:
        self._each(request, queryset, brands.approve_batch, "Approved {count} batch(es).")

    @admin.action(
        description=_("Reject all brands of the selected batches"), permissions=("review",)
    )
    def reject_selected(
        self, request: HttpRequest, queryset: "models.QuerySet[BrandBatch]"
    ) -> None:
        self._each(request, queryset, brands.reject_batch, "Rejected {count} batch(es).")

    def has_review_permission(self, request: HttpRequest) -> bool:
        """Who may approve or reject a batch: whoever may change brands."""
        return request.user.has_perm("catalog.change_brand")


@admin.register(Manufacturer)
class ManufacturerAdmin(admin.ModelAdmin[Manufacturer]):
    list_display = ("legal_name", "country", "url")
    search_fields = ("legal_name",)
    readonly_fields = TIMESTAMPS
    fields = ("legal_name", "url", "same_as", "country", *TIMESTAMPS)

    def save_model(
        self,
        request: HttpRequest,
        obj: Manufacturer,
        form: forms.ModelForm[Manufacturer],
        change: bool,
    ) -> None:
        brands.save_manufacturer(obj, ctx=audit_context(request))

    def delete_model(self, request: HttpRequest, obj: Manufacturer) -> None:
        brands.delete_manufacturer(obj, ctx=audit_context(request))

    def delete_queryset(
        self, request: HttpRequest, queryset: "models.QuerySet[Manufacturer]"
    ) -> None:
        ctx = audit_context(request)
        for maker in queryset:
            brands.delete_manufacturer(maker, ctx=ctx)
