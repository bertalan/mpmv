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

from typing import Any, ClassVar

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.utils.translation import gettext_lazy as _
from modelcluster.fields import ParentalKey
from wagtail.admin.panels import FieldPanel, FieldRowPanel, InlinePanel
from wagtail.contrib.forms.models import AbstractForm, AbstractFormField
from wagtail.fields import RichTextField

from apps.accounts.captcha import captcha_passes
from apps.core.public_forms import Protection, public_form

DEFAULT_RETENTION_DAYS = 90
MAX_RETENTION_DAYS = 365


class FormField(AbstractFormField):  # type: ignore[misc]
    """One field of a form page, added by the editor in the page editor."""

    page = ParentalKey("cms.FormPage", on_delete=models.CASCADE, related_name="form_fields")


@public_form(
    protection=Protection.CAPTCHA,
    purpose="Editor-built form pages: any form an editor publishes (contact, request, survey)",
    task="CMS.13",
    rate_limit="the captcha only (a token is single use)",
    templates=("cms/form_page.html",),
)
class FormPage(AbstractForm):  # type: ignore[misc]
    """A page whose form the editor builds, with a Turnstile check and a retention period."""

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
    landing_page_template = "cms/form_page_landing.html"

    class Meta:
        verbose_name = _("form page")

    def serve(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if request.method == "POST" and not captcha_passes(request):
            form = self.get_form(request.POST, request.FILES, page=self, user=request.user)
            context = self.get_context(request)
            context["form"] = form
            context["captcha_failed"] = True
            return TemplateResponse(request, self.get_template(request), context, status=400)
        response: HttpResponse = super().serve(request, *args, **kwargs)
        return response
