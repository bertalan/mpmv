"""Editor-built form pages (CMS.13, owner decision D-36) on Wagtail's form builder.

An editor adds a "Form page" under the home page, builds the fields in the editor (text, email,
choices and so on), and publishes it: no code. Everything a stranger can post goes through one
door, `FormPage.serve`:

- **Spam check**: Cloudflare Turnstile through `apps.accounts.captcha` (the `CaptchaVerifier`
  interface). A POST without a valid token is refused with a 400 and stores nothing. The check
  runs before the form is read, like the other public forms (SEC.13).
- **Retention**: each page keeps its submissions `retention_days` days (default 90, at most
  365: they are contact data, not an order record, so D-29 and D-30 do not apply to them). The
  daily job `cms.purge_form_submissions` deletes older ones
  (`services.purge_expired_form_submissions`).
- **Register**: the page type carries a `public_form` declaration, so core.E704 does not flag it
  and the register of public forms (docs/dev/public-forms.md) lists it with no manual step.

No email is sent by the page itself (Wagtail's email form is not used: its mail would bypass the
outbox, ARC.16). Staff read the submissions under Forms in the Wagtail admin.
"""

import datetime as dt
import math
from typing import Any, ClassVar

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, FieldRowPanel, InlinePanel
from wagtail.contrib.forms.forms import FormBuilder
from wagtail.contrib.forms.models import AbstractForm, AbstractFormField
from wagtail.fields import RichTextField

from apps.accounts.captcha import captcha_passes
from apps.core.http import client_ip
from apps.core.nfc_forms import NFCFormMixin, NFCPageForm
from apps.core.public_forms import Protection, public_form
from apps.core.services import consume_rate_limit
from apps.core.throttling import ip_bucket

DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 365
RATE_WINDOW = dt.timedelta(minutes=15)
# Longest answer per field type; Wagtail's form builder sets none. Totals are capped as well
# (`settings.FORM_PAGE_MAX_SUBMISSION_BYTES`).
FIELD_MAX_LENGTH = {"singleline": 255, "multiline": 5000, "email": 254, "url": 2000}


class BoundedFormBuilder(FormBuilder):  # type: ignore[misc]
    """Wagtail's builder with a `max_length` on every text-like field."""

    def get_form_class(self) -> Any:
        """The submission form stores its text in NFC (I18N.10)."""
        base = super().get_form_class()
        return type(base.__name__, (NFCFormMixin, base), {})

    def create_singleline_field(self, field: Any, options: dict[str, Any]) -> Any:
        return super().create_singleline_field(
            field, {**options, "max_length": FIELD_MAX_LENGTH["singleline"]}
        )

    def create_multiline_field(self, field: Any, options: dict[str, Any]) -> Any:
        return super().create_multiline_field(
            field, {**options, "max_length": FIELD_MAX_LENGTH["multiline"]}
        )

    def create_email_field(self, field: Any, options: dict[str, Any]) -> Any:
        return super().create_email_field(
            field, {**options, "max_length": FIELD_MAX_LENGTH["email"]}
        )

    def create_url_field(self, field: Any, options: dict[str, Any]) -> Any:
        return super().create_url_field(field, {**options, "max_length": FIELD_MAX_LENGTH["url"]})


class FormField(AbstractFormField):  # type: ignore[misc]
    """One field of a form page, added by the editor in the page editor."""

    page = ParentalKey("cms.FormPage", on_delete=models.CASCADE, related_name="form_fields")


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Editor-built form pages: any form an editor publishes (contact, request, survey)",
    task="CMS.13",
    rate_limit="per client IP (settings.FORM_PAGE_RATE_LIMIT per 15 minutes), then the captcha",
    templates=("cms/form_page.html",),
)
class FormPage(AbstractForm):  # type: ignore[misc]
    """A page whose form the editor builds, with a Turnstile check and a retention period."""

    base_form_class = NFCPageForm

    intro = RichTextField(
        _("introduction"),
        blank=True,
        features=["bold", "italic", "link", "ol", "ul"],
        help_text=_(
            "A short text above the form. Say who reads the answers and what you do with them. "
            "If you leave it empty, the form starts right under the title."
        ),
    )
    thank_you_text = RichTextField(
        _("thank-you text"),
        blank=True,
        features=["bold", "italic", "link"],
        help_text=_(
            "Shown after a successful submission. If you leave it empty, a short standard "
            "message is shown."
        ),
    )
    submit_label = models.CharField(
        _("button label"),
        max_length=60,
        blank=True,
        help_text=_(
            "The text on the send button, for example 'Send your question'. If you leave it "
            "empty, the button says 'Send'."
        ),
    )
    retention_days = models.PositiveSmallIntegerField(
        _("keep submissions for (days)"),
        default=DEFAULT_RETENTION_DAYS,
        validators=[MinValueValidator(1), MaxValueValidator(MAX_RETENTION_DAYS)],
        help_text=_(
            "How many days each submission is kept before it is deleted for good, from 1 to "
            "365. Keep it as short as your work allows. The default is 90 days."
        ),
    )

    content_panels: ClassVar[list[Any]] = [
        *AbstractForm.content_panels,
        FieldPanel("intro"),
        InlinePanel("form_fields", label=_("Form fields")),
        FieldPanel("thank_you_text"),
        FieldRowPanel([FieldPanel("submit_label"), FieldPanel("retention_days")]),
    ]
    parent_page_types: ClassVar[list[str]] = ["cms.HomePage", "cms.StandardPage"]
    subpage_types: ClassVar[list[str]] = []
    template = "cms/form_page.html"
    form_builder = BoundedFormBuilder
    landing_page_template = "cms/form_page_landing.html"

    class Meta:
        verbose_name = _("form page")

    def serve(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.method == "POST":
            refused = self._refuse_before_reading(request)
            if refused is not None:
                return refused
        if request.method == "POST" and not captcha_passes(request):
            form = self.get_form(request.POST, request.FILES, page=self, user=request.user)
            context = self.get_context(request)
            context["form"] = form
            context["captcha_failed"] = True
            return TemplateResponse(request, self.get_template(request), context, status=400)
        response: HttpResponse = super().serve(request, *args, **kwargs)
        return response

    def _refuse_before_reading(self, request: HttpRequest) -> HttpResponse | None:
        """429 over the per-IP limit, 400 over the size cap; both store nothing."""
        wait = consume_rate_limit(
            f"form-page:ip:{ip_bucket(client_ip(request)) or 'unknown'}",
            limit=settings.FORM_PAGE_RATE_LIMIT,
            window=RATE_WINDOW,
        )
        if wait:
            response = HttpResponse(
                str(_("Too many submissions, try again shortly.")),
                status=429,
                content_type="text/plain; charset=utf-8",
            )
            response["Retry-After"] = str(max(1, math.ceil(wait.total_seconds())))
            return response
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > settings.FORM_PAGE_MAX_SUBMISSION_BYTES:
            return HttpResponse(
                str(_("This submission is too large.")),
                status=400,
                content_type="text/plain; charset=utf-8",
            )
        return None
