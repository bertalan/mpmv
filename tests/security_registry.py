"""What the endpoint security tests (SEC.5) accept, per endpoint, with the reason.

`tests.integration.test_endpoint_security` calls every URL pattern of the root URLconf and every
API v1 operation and fails when an endpoint answers a caller it should refuse. When you add:

- a page that anyone may open without signing in: list it in `PUBLIC` with the reason. A new
  route that answers an anonymous visitor with anything but a refusal (401, 403, 404, 405 or a
  redirect to a sign-in page) turns CI red until it is listed here;
- a view exempt from CSRF (`csrf_exempt`): list it in `CSRF_EXEMPT` with what protects it
  instead (a signature, a bearer key). Every other view must refuse a POST without the token;
- a GET that writes: avoid it. A GET must not insert, update or delete rows other than the
  session (`GET_WRITES_ALLOWED`); an exception needs an entry in `GET_WRITES_BY_ENDPOINT` that
  says why a request forged from another site gains nothing from it.

- a route that carries an identifier: the anonymous and customer checks call it with a real
  object of Vendor B, so a 404 can only mean "not found for this caller". `VendorScoped` and
  `StaffOnly(make=...)` declarations in `tests.tenancy_registry` say how to make that object;
  for any other route, `OBJECT_PATHS` builds the URL parameters from Vendor B's data. A route
  called with placeholder parameters must refuse before it looks anything up (a redirect to
  sign-in, 401, 403 or 405): a 404 there fails unless `PLACEHOLDER_404` says why it is safe.

Keys are the namespaced URL name, or the route when the pattern has no name (as in
`tests.tenancy.Endpoint.key`). Every reason is a sentence: an empty one fails
`test_every_allow_list_entry_has_a_reason`, and each test it names in backticks must exist.
"""

from collections.abc import Callable

from apps.accounts.tests.factories import TOTPDeviceFactory
from apps.compliance.tests.factories import NoticeFactory
from tests.tenancy import CrossTenant

PUBLIC_CATALOG = "Public catalog listing (CAT.8): published products of active vendors only."
PUBLIC_CMS = "Public CMS page (CMS.1)."
MEDIA = (
    "Public image files (THM.12, apps.core.media): published images and renditions, served to "
    "anyone as on STAGE/LIVE through Caddy. The path names a file in the image folders only, not "
    "an object a caller could swap for another tenant's; anything outside them is a 404 "
    "(tests/integration/test_modern_image_formats.py)."
)
SIGN_IN = "Sign-in page: must answer before the visitor is signed in."
PASSWORD_RESET = "Password reset flow of the editors' admin: answers before sign-in."

# Endpoints an anonymous visitor may reach (a 2xx, or a redirect that is not to a sign-in page).
PUBLIC: dict[str, str] = {
    "healthz": "Liveness probe with a constant answer.",
    "readyz": "Readiness probe: a status only, no data.",
    "robots_txt": "robots.txt (SEO.8): constant text for crawlers, no vendor data.",
    "email_webhook": "Email provider feedback: refused unless the payload is signed (ARC.16).",
    "home": "Public home page.",
    "media": MEDIA,
    "catalog:category": PUBLIC_CATALOG,
    "catalog:brand": PUBLIC_CATALOG,
    "catalog:term": PUBLIC_CATALOG,
    "catalog:vendor": PUBLIC_CATALOG,
    "switch_language": "Stores the visitor's language choice in a cookie.",
    "action_link:open": "Secure action link (CORE.15): the signed token is the credential.",
    "action_link:continue": "Secure action link (CORE.15): the signed token is the credential.",
    "garage_save": (
        "Anonymous garage cookie control (FIT.6, Q-G1): a POST that only writes or clears the "
        "visitor's own technical cookie, CSRF-checked, nothing stored or logged on the server."
    ),
    "garage_remove": (
        "Anonymous garage cookie control (FIT.6, Q-G1): a POST that only writes or clears the "
        "visitor's own technical cookie, CSRF-checked, nothing stored or logged on the server."
    ),
    "garage_forget": (
        "Anonymous garage cookie control (FIT.6, Q-G1): a POST that only writes or clears the "
        "visitor's own technical cookie, CSRF-checked, nothing stored or logged on the server."
    ),
    "dsa_notice": (
        "Public report form for illegal content (DSA art. 16, CMP.5): the page is public by "
        "design; the POST is CSRF-checked, behind Turnstile and rate limited per address, and "
        "it only stores a notice."
    ),
    "dsa_notice_received": (
        "Thanks page after a report (CMP.5): shows only the reference kept in the visitor's "
        "own session, a 404 without one."
    ),
    "dsa_contact_points": (
        "DSA points of contact (CMP.5): the operator's own contact data, read-only, public by law "
        "(DSA art. 11 and 12)."
    ),
    "vendors:apply": (
        "Public 'Sell with us' application form (VEN.22): the page is public by design; the POST "
        "is CSRF-checked, rate limited per IP and behind Turnstile, and it creates an inactive "
        "account that only the emailed link activates."
    ),
    "vendors:apply_sent": "Static 'check your email' page of the application (VEN.22).",
    "vendors:apply_cancel": (
        "'This was not me' page of the application mails (VEN.22, D-24): a GET shows a button "
        "or the same neutral page whatever the state; only a POST with CSRF acts, and only for "
        "the account whose email-bound cancel link was opened in this session."
    ),
    "login": SIGN_IN,  # the Manager's one login (CORE.17)
    "admin:login": SIGN_IN,
    "admin:logout": "Sign-out: without a session it only redirects to the admin home.",
    "api-v1:api-root": "API root: links only.",
    "api-v1:openapi-json": "API description, the same for every caller.",
    "api-v1:openapi-view": "Interactive API documentation page.",
    "wagtail_serve": PUBLIC_CMS,
    "wagtailcore_login": "Sign-in for password-protected CMS pages.",
    "wagtailcore_authenticate_with_password": "Password form of a private CMS page.",
    "wagtailadmin_login": SIGN_IN,
    "wagtailadmin_password_reset": PASSWORD_RESET,
    "wagtailadmin_password_reset_done": PASSWORD_RESET,
    "wagtailadmin_password_reset_confirm": PASSWORD_RESET,
    "wagtailadmin_password_reset_complete": PASSWORD_RESET,
    "wagtailadmin_sprite": "The admin's icon sprite, the same for everyone.",
    "wagtailadmin_javascript_catalog": "Admin JavaScript translation catalog, no data.",
}

# Views that do not check the CSRF token, and what protects them instead.
CSRF_EXEMPT: dict[str, str] = {
    "email_webhook": "Server-to-server call from the email provider, verified by its signature.",
}
# Every API v1 operation (django-ninja) is CSRF-exempt: it authenticates with a bearer API key
# only (CORE.13), never with the session cookie, so a cross-site form cannot act through it.
API_CSRF_REASON = "API v1: bearer API key only, the session cookie never authenticates."

# Tables any request may write: the session store (sign-in state, language, messages).
GET_WRITES_ALLOWED: frozenset[str] = frozenset({"django_session"})

# IDOR review: a web endpoint or API operation whose URL carries an identifier (a public id, a
# slug, a token, a page id) must be `VendorScoped` or `StaffOnly` in `tests.tenancy_registry`,
# where the cross-tenant tests reach it with another vendor's object, or be listed here with what
# keeps one user from reaching another's object through it.
IDOR_REVIEWED: dict[str, str] = {
    "media": MEDIA,
    "mfa:remove_device": (
        "The factor is looked up among the signed-in user's own (CORE.4); another user's factor "
        "answers 404 (`test_a_user_cannot_remove_another_users_second_factor`)."
    ),
    "action_link:open": (
        "The signed token is bound to one user and one object (CORE.15); a token of another "
        "user or object is refused (apps/accounts/tests/integration/test_action_links.py)."
    ),
    "vendor_claim:claim": (
        "The page answers only for the vendor whose email-bound claim link was opened in this "
        "session (VEN.24); another vendor's public id is refused with 403 "
        "(`test_a_claim_link_opens_only_its_own_vendor`)."
    ),
    "vendors:apply_cancel": (
        "Acts only for the account whose email-bound cancel link was opened in this session "
        "(VEN.22); any other key gets the same neutral page and nothing changes "
        "(`test_the_cancel_page_gives_nothing_away_for_a_random_key`)."
    ),
    "vendors:apply_confirm": (
        "The page answers only for the account whose email-bound confirmation link was opened in "
        "this session (VEN.22); any other key is refused with 403 "
        "(`test_the_confirm_page_gives_nothing_away_for_a_random_key`)."
    ),
    "catalog:category": "Public listing: the slugs name public categories.",
    "catalog:brand": "Public listing: the slug names a public brand.",
    "catalog:vendor": "Public storefront: the slug names an active vendor's public shop.",
    "catalog:term": "Public listing: the slugs name public taxonomy terms.",
    "wagtailcore_authenticate_with_password": (
        "Wagtail's password form for a restricted CMS page: the ids only say which page the "
        "password unlocks; the password is the credential."
    ),
}

# URL parameters naming a real object of Vendor B (or of its owner), for routes with an identifier
# that are neither `VendorScoped` nor `StaffOnly(make=...)` in `tests.tenancy_registry`.
ObjectKwargs = Callable[[CrossTenant], dict[str, str]]
OBJECT_PATHS: dict[str, ObjectKwargs] = {
    "mfa:remove_device": lambda tenants: {
        "kind": "totp",
        "public_id": str(TOTPDeviceFactory.create(user=tenants.b.owner).pk),
    },
    "vendor_claim:claim": lambda tenants: {"vendor_key": str(tenants.b.vendor.public_id)},
    "manager:platform:notice": lambda tenants: {"public_id": str(NoticeFactory.create().public_id)},
    "vendors:apply_confirm": lambda tenants: {"user_key": str(tenants.b.owner.public_id)},
    "vendors:apply_cancel": lambda tenants: {"user_key": str(tenants.b.owner.public_id)},
}

# Routes with an identifier, called with placeholder parameters, that may answer 404 to a caller
# who is not signed in or not allowed, with why that 404 hides nothing.
PLACEHOLDER_404: dict[str, str] = {
    "media": MEDIA,
    "wagtailadmin_password_reset_confirm": (
        "Password reset confirmation of the editors' admin, public by design (PUBLIC): the uid "
        "and token in the URL are the credential, not a reference to an object a caller could "
        "swap; a wrong pair is refused whoever asks."
    ),
}

# Tables a GET of one endpoint may also write, with the reason.
GET_WRITES_BY_ENDPOINT: dict[str, dict[str, str]] = {
    "wagtailadmin_account": {
        "wagtailusers_userprofile": (
            "Wagtail creates the signed-in user's own preferences row, with defaults, the first "
            "time it is read; nothing another user or site can choose."
        ),
    },
    "mfa:totp": {
        "otp_totp_totpdevice": (
            "The pending, unconfirmed authenticator device of this session's enrolment (CORE.4): "
            "it grants nothing until confirmed by a POST with a code, and a repeated GET in the "
            "same session reuses it."
        ),
    },
}
