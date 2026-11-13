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
    "payments_webhook": (
        "Payment provider events (PAY.2): refused unless the payload is signed and fresh; with "
        "no provider configured it answers 404."
    ),
    "home": "Public home page.",
    "media": MEDIA,
    "catalog:category": PUBLIC_CATALOG,
    "catalog:product": PUBLIC_CATALOG,
    "catalog:brand": PUBLIC_CATALOG,
    "catalog:term": PUBLIC_CATALOG,
    "catalog:vendor": PUBLIC_CATALOG,
    "orders:cart": (
        "Cart page (ORD.5): shows the visitor's own cart, found from their session; a POST "
        "changes only that cart, is CSRF-checked and has a per-IP budget."
    ),
    "orders:checkout": (
        "Checkout details form (ORD.5): public by design for guests; the page redirects to the "
        "cart without one; the POST is CSRF-checked, behind Turnstile and a per-IP budget, and "
        "stores only in the visitor's session."
    ),
    "orders:review": (
        "Checkout review (ORD.5): redirects to the cart or the details without them; the POST "
        "that places the order is CSRF-checked, behind Turnstile and a per-IP budget."
    ),
    "orders:shipping": (
        "Shipping choice of the checkout (ORD.5): a POST that redirects a visitor without a "
        "cart; it stores the choice in the visitor's session only, with CSRF and a per-IP budget."
    ),
    "switch_language": "Stores the visitor's language choice in a cookie.",
    "consent_save": (
        "Cookie consent choice (SEO.5): the banner on every page posts to it; it stores a random "
        "id and the categories chosen and sets the visitor's own consent cookie."
    ),
    "consent_preferences": (
        "Cookie settings page (SEO.5): shows the visitor's own choice from their own cookie, "
        "never cached, and no data of anyone else."
    ),
    "action_link:open": "Secure action link (CORE.15): the signed token is the credential.",
    "action_link:continue": "Secure action link (CORE.15): the signed token is the credential.",
    "dsa_notice": (
        "Public report form for illegal content (DSA art. 16, CMP.5): the page is public by "
        "design; the POST is CSRF-checked, behind Turnstile and rate limited per address, and "
        "it only stores a notice."
    ),
    "dsa_notice_received": (
        "Thanks page after a report (CMP.5): shows only the reference kept in the visitor's "
        "own session, a 404 without one."
    ),
    "withdrawal": (
        "Online withdrawal function, step one (CMP.6): the page is public by design and shows "
        "no order data; the POST is CSRF-checked, behind Turnstile and rate limited per "
        "address, and only a matching order number and email lead to the choice page."
    ),
    "withdrawal_confirm": (
        "Online withdrawal function, step two (CMP.6): a GET answers 405; the POST needs the "
        "CSRF token and the signed 30-minute token minted by a successful lookup."
    ),
    "withdrawal_done": (
        "Acknowledgement page of the withdrawal function (CMP.6): shows only the statements "
        "kept in the visitor's own session, a 404 without one."
    ),
    "consumer_information": (
        "Pre-contractual information (CMP.6, directive 2011/83/EU art. 49): the same legal text "
        "for every visitor, public by law."
    ),
    "subprocessors": (
        "Sub-processor register (CMP.4): static reviewed data, read-only, public by design "
        "(GDPR art. 13 and 28)."
    ),
    "privacy_erase_done": (
        "Static confirmation after an account erasure (CMP.4): the user is already signed out "
        "and the account blocked, so the page cannot ask for a session; it shows no data."
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
    "consent_save": (
        "The consent banner is the same markup on every cached page, so it cannot carry a CSRF "
        "token. The view refuses a request whose Origin (or Referer) is not this site, so a form "
        "on another site cannot give or withdraw consent; it is rate limited per IP "
        "(`test_a_cross_site_post_cannot_give_or_withdraw_consent`)."
    ),
    "payments_webhook": (
        "Server-to-server call from the payment provider, verified by its signature and a "
        "timestamp tolerance (PAY.2)."
    ),
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
    "customer_order": (
        "The order is looked up among the signed-in customer's own (ORD.7); a stranger's order "
        "and a guest order answer 404 (`test_a_stranger_s_order_is_a_404_like_an_unknown_one`)."
    ),
    "mfa:remove_device": (
        "The factor is looked up among the signed-in user's own (CORE.4); another user's factor "
        "answers 404 (`test_a_user_cannot_remove_another_users_second_factor`)."
    ),
    "action_link:open": (
        "The signed token is bound to one user and one object (CORE.15); a token of another "
        "user or object is refused (apps/accounts/tests/integration/test_action_links.py)."
    ),
    "manager:platform:profile_confirm": (
        "Platform space, for staff holding the revert permission only (a vendor member gets 403 "
        "from the gate); the entry is an audit entry of any vendor, which staff may review by "
        "design."
    ),
    "manager:platform:profile_revert": (
        "Platform space, for staff holding the revert permission only (a vendor member gets 403 "
        "from the gate, `test_a_vendor_member_cannot_reach_the_page_or_the_revert`); the entry is "
        "an audit entry of any vendor, which staff may review by design."
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
    "catalog:product": (
        "Public product page (CAT.7): the slug names a published product of an active vendor; "
        "a draft, a hidden product and an inactive vendor answer 404."
    ),
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
