# 02 — Identity, roles, vendor isolation, API and backoffice

## 2.1 How it works today

- A single WordPress login for customers, vendors and staff; a single `/wp-admin` for all roles.
- WCFM vendors have the `manage_woocommerce` capability: every "admin" check must use
  `manage_options` (bug documented in the VAT plugin).
- WCFM staff and groups are not configured; the only use is the `wcfmgs_user_capability` filter for
  Vendor E, which broke with the WCFM update.
- API: `api.php` with routes included dynamically from `routes/{route}.php`; the authentication
  model must be redone (details in the restricted document).
- Customer support via "Login as User".

## 2.2 How it will work

### Models

```python
class User(AbstractUser):  # accounts — custom from the very first migrate
    email = EmailField(unique=True)  # login via email
    preferred_language = CharField(choices=LANGUAGES, default="en")
    is_staff_member: bool  # platform staff (backoffice)
    mfa_enabled: bool


class VendorMember(Model):  # vendors — user ↔ vendor membership
    vendor = FK(Vendor)
    user = FK(User)
    role = CharField(choices=["owner", "staff"])
    # owner: payouts, IBAN, VAT regime, users, storefront profile; staff: orders, quotes, products


class ApiClient(Model):  # api — machine credential (pipelines, MCP, integrations)
    name
    vendor = FK(Vendor, null=True)  # null = platform client
    scopes = ArrayField(CharField())  # e.g. ["catalog:write", "stock:write"]
    key_prefix = CharField(8)
    key_hash = CharField()  # never the key in plain text
    expires_at
    last_used_at
    allowed_ips = ArrayField(InetAddress, null=True)
    is_active: bool
```

### Roles

| Role | Where they work | Can |
|---|---|---|
| Anonymous | Site | Browse, cart, guest checkout, quote request |
| Customer | Customer area | Orders, quotes, returns/withdrawal, personal data, GDPR export |
| Vendor owner | Manager, shop space (`/manager/shop/`) | Everything concerning **their own** vendor, including financial settings |
| Vendor staff | Manager, shop space (`/manager/shop/`) | Orders, quotes, products; no payouts, IBAN or tax regime |
| Editor | Wagtail admin | Pages, blog, SEO copy, translations, each editor role limited to its part of the page tree (Q-58) |
| Configuration and tax | Manager, platform space (`/manager/platform/`) | Operator data, countries, languages, currency, VAT rates, regimes, commissions, shipping rules (owner decision 2026-11-09) |
| Catalog and moderation | Manager, platform space | All vendors' products, categories, attributes, taxonomies, imports; approval of vendor events and content (with the Wagtail "Event review" role) |
| Finance and payments | Manager, platform space | Vendor payouts, balances, refunds, reconciliation, DAC7 and tax reports; no configuration access |
| Support | Manager, platform space | Orders, customers and vendors read-only, plus audited support actions (unlock an account, resend a link, impersonation CORE.10) |
| Platform owner | Manager, platform space | Assigns the platform staff roles, owner-only settings: allowed vendor establishment countries (CMP.13), approval of staff proposals to turn EPR checks on or off per country (CMP.11), vendor lifecycle decisions. A dedicated permission, never implied by staff or admin access; it never approves its own proposal |
| Technical superuser | Django admin (restricted network) | Maintenance; exceptional, tracked use |

The four platform staff roles replace the earlier single "staff operator" role (owner decision
2026-11-09, CORE.18). A person can hold several roles; each role adds sections, never removes a
check. What every role can open is the [role-permission matrix](#role-permission-matrix-core18).

### Tenant (vendor) isolation

A single mechanism, used everywhere (views, API, MCP, exports, admin search):

```python
class VendorScopedQuerySet(QuerySet):
    def for_actor(self, actor: Actor) -> "VendorScopedQuerySet":
        """Platform staff permission: everything. Vendor member: only their own records. Others: nothing."""

def get_for_actor_or_404(qs, actor, **lookup):  # 404 (not 403) to prevent enumeration
```

- Every model with a `vendor` FK uses this manager.
- **Platform staff** (owner decision Q-34) means a user who holds a dedicated permission, for
  example `vendors.view_all_vendors`, given only to the platform staff roles that need it
  (catalog and moderation, finance and payments, support, platform owner; CORE.18). Access to the
  Wagtail or Django admin (`is_staff`) does not count: an editor or an external copywriter who
  writes pages and blog posts in Wagtail sees no vendor's orders, customers or bank data. Today
  `apps/vendors/scoping.py` (`_is_platform_staff`) still checks `is_staff`; CORE.8 replaces it
  with the permission.
- **Mandatory negative tests** for every endpoint: "Vendor A requests Vendor B's object → 404".
- Vendor documents (KYC, signed contracts, commission and payout statements) can be downloaded only from views that check permissions; never
  public URLs.

### Authentication

- Web session: modern password handling (check against compromised passwords), **mandatory TOTP/passkey
  MFA** for everyone who reaches a management area, progressive lockout per account and IP.
- Unlocking (owner decision Q-18): the Support role unlocks a locked account from the Manager
  platform space, with an `AuditLog` entry, and a completed password reset clears the account's
  lockout (CORE.8).
- Step-up (re-authentication) for: IBAN change, payout, rate changes (commission rates and VAT
  rates alike, owner decision MQ-2), customer data export.

### Multi-factor authentication

MFA is mandatory for platform staff, superusers and every vendor member, owners and staff alike
(owner decision Q-09). A user in one of these groups who has no confirmed second factor is sent to
enrolment before any page; one who has a factor must verify it after the password. Customers with
no vendor membership and no staff flag are not forced. Staff and superusers are always covered in
`apps.accounts.services.mfa_required`. Further groups come from the dotted paths in
`MFA_REQUIRED_POLICIES` in the settings; vendor members are added there by
`apps.vendors.services.is_vendor_member`. A policy can only add a requirement, never remove one.

The second factor is a TOTP authenticator app or a passkey. The confirmed parameters are:

| Parameter | Value | Where it is configured |
|---|---|---|
| Step-up window | 300 seconds since the last verification | `MFA_STEP_UP_MAX_AGE` in the settings |
| Recovery codes | 10 codes of 16 characters each | `RECOVERY_CODE_COUNT` and `RECOVERY_CODE_LENGTH` in `apps/accounts/engine.py` |
| TOTP codes | 6 digits, 30 second step | django-otp defaults, stored per device on `TOTPDevice` |
- API: `Authorization: Bearer mpmv_<prefix>_<secret>` header, constant-time comparison,
  scope checked per route, rate limit per API key with counters in a cache shared by every app
  process (CORE.12, owner decision Q-18), logging of every mutating call.
- API rate limit (CORE.12): fixed windows per key, counted in PostgreSQL (`ApiRateCounter`, one
  atomic upsert per call), so every app process shares one counter and no Redis is needed.
  `API_RATE_LIMIT` is the key-wide limit of every key, set per environment with
  `MPMV_API_RATE_LIMIT`, default `120/minute` (owner decision MQ-4; CORE.12 shipped
  `600/minute`, CORE.16 lowered it);
  `API_RATE_LIMIT_SCOPES` gives a costly scope its own counter and limit for every key;
  `API_RATE_LIMIT_CLIENTS` replaces the key-wide limit of one key, by `ApiClient` public id.
  Staff can raise or lower the limit of one key from the admin (`ApiClient.rate_limit`), with
  the change permission, audited (`api.client.rate_limit_changed`) and behind an MFA step-up
  (CORE.16). Precedence of a key's key-wide limit: that override, then
  `API_RATE_LIMIT_CLIENTS`, then `API_RATE_LIMIT`. A scope with its own limit keeps it for every
  key, a raised one included. A key whose own limit (override or `API_RATE_LIMIT_CLIENTS`) is
  below `API_RATE_LIMIT` (compared as calls per second) is also capped on such scopes, because
  those calls count on its key-wide counter too. A key on the default limit, or one raised to or
  above it, keeps separate budgets, so a raise never leaves a key fewer calls than a default key
  (pending owner confirmation of this precedence). Only
  calls of an accepted key count (401/403 refusals do not). Over the limit the answer is a 429
  `rate_limited` problem with `Retry-After` (seconds to the end of the window); a mutating call
  refused this way is in the mutation log.
- Vendor key policy (CORE.14): a vendor key is issued only while its vendor is active, and holds
  only the vendor-grantable scopes (`VENDOR_GRANTABLE_SCOPES` in `apps/api/engine.py`, an
  allow-list checked at issuance and again on every request, so a key issued before a scope
  became platform-only cannot use it; `manage.py vendor_key_scopes` lists such keys). Every
  other scope is platform-only. The vendor's status is read on every call: only an active
  vendor's key writes; an applicant, onboarding or suspended vendor's key only reads (403 on
  POST, PUT, PATCH and DELETE, dry-runs included); a closed vendor's key gets 401. Closing a
  vendor also revokes each of its keys, one AuditLog entry per key, so reopening it (VEN.21)
  revives none: the vendor issues new keys once active again. Vendor-scoped
  write services reached by API v1 read the status again inside the write transaction (row
  read `FOR SHARE`), so a vendor suspended after its key was authenticated gets that write
  refused. Deleting a vendor writes one AuditLog entry per key it deletes.
- Impersonation for support (equivalent of Login as User): the roles the impersonation policy
  allows, time-limited, with a reason, a banner, audit and a hard floor (CORE.10, see Backoffice).

### Secure action links (CORE.15)

Emails that ask a user to approve or decline something (bundle funding proposals CAT.18, CMP.10
attestation reminders, VEN.16 contract re-acceptance, EVT.11 event moderation notices) use one
reusable mechanism: a service in `accounts` plus an email helper on top of the ARC.16 sender.

- **The link** carries a signed token, bound to the action object ID, the recipient user and the
  object version. It expires after a configurable time (default 72 h) and is single-use for
  sign-in. A new version of the object invalidates older tokens.
- **Prefetch.** Mail scanners (Outlook Safe Links, antivirus) prefetch GET and HEAD links. A GET or
  HEAD never consumes the token and never changes state: it shows an intermediate page whose
  "Continue" button POSTs, and only that POST consumes the sign-in use.
- **Valid session:** the user lands directly on the action page.
- **No session:** the token is a one-time sign-in step (a magic link that proves control of the
  mailbox), so no password is asked. It never replaces MFA: before the approve or decline POST
  the page requires an MFA step-up (TOTP or passkey, step-up window 300 s, CORE.4). Someone who
  holds only the mailbox cannot approve.
- **Approval** is always a POST with CSRF from the action page, never a GET.
- **Misuse.** An expired, used or mismatched token (another user, an old version) sends the user
  to the normal login and then to the same action page; the attempt is written to the AuditLog.
- **Implementation (CORE.15).** The app that owns the action object registers a purpose in
  `accounts` (`register_action_purpose`: current version, action page URL, who may act) and
  emails links with `send_action_link_email` through the ARC.16 outbox. The token is signed with
  Django signing under its own salt; the Continue POST carries it in the form body, is rate
  limited per IP and records the single use as a unique row (`ActionLinkUse`, nonce digest
  only). Link pages send `Referrer-Policy: strict-origin` (never the path, which holds the token;
  `no-referrer` would make browsers send `Origin: null` and fail the CSRF check) and are never
  cached. A user signed in as someone else is refused, never switched. A recipient with no
  confirmed second factor gets no passwordless sign-in (the first factor can be enrolled without
  other proof, so the link alone would be enough to bind an attacker's authenticator): the link
  sends them to the normal login. Request logs (`django.request`, gunicorn's access log) redact
  the token segment of the link path.
- **Vendor claim (VEN.24, owner decision MQ-1).** A vendor imported without an account holder is
  claimed through the same kind of link, bound to the vendor and to a claim version instead of a
  recipient user, and sent only to the vendor's legal email of record. There is no account yet,
  so the claimant sets a password and enrols their own MFA before anything else; the link never
  signs anyone in to an existing account. Regenerating the claim bumps its version, so the old
  link stops working.

### API v1 (replaces `api.php`)

The ~85 "recurring" routes are consolidated into ~15 REST resources:

| Resource | Operations | Replaces |
|---|---|---|
| `/products` | bulk upsert matched by vendor and SKU match key (import matching only, [03](03-catalog.md)), get by `public_id`, paginated export, delisting | `create_product_full`, `update_product_*_relationships`, `get_all_product_*`, `export_vendor_products`, `delete_product`, `trash_products` |
| `/products/{id}/variants` | variant upsert, axes, default (`{id}` is the product `public_id`, never the SKU) | `convert_to_variable_product` |
| `/stock`, `/prices` | bulk update with thresholds | `batch_update_stock`, `get_vendor_product_stock/prices` |
| `/media` | direct upload (presigned URL), cover/gallery assignment | `upload_media`, `upload_images_from_folder`, `convert_images_webp`, `regenerate_product_thumbnails` |
| `/taxonomy/{categories,taxonomies,terms,brands}` | CRUD with translations and SEO copy | `create_categories`, `create_tags`, `*_tag_translations`, `update_*_descriptions`, `assign_*` |
| `/taxonomy/assign-by-content` | keyword-based assignment with dry-run | `assign_brand_tags_by_content` |
| `/shipping/config`, `/shipping/simulate` | validated read/write, simulation | `*_custom_shipping_vendors_config*`, `manage_gls_shipping_zones`, `test` |
| `/tax/rates`, `/tax/simulate` | rates with validity, cart simulation | `sync_eu_standard_vat_rates`, `simulate_cart_tax` |
| `/vendors/{id}` | profile, policies, logo (per language) | `update_vendor_*` |
| `/reviews` | supplier review import | `manage_product_reviews` |
| `/content/posts` | blog drafts | `create_post`, `update_post` |
| `/vendors/{id}/closures` | holiday and closure schedule: CRUD, dry-run, audit (VEN.20, [04](04-vendors-commissions-ledger.md)) | new |
| `/events` | vendor events (promotions, trade fairs and meetings, other) as `EventPage` drafts in any enabled language, with translations: CRUD, submit, cancel, dry-run ([15](15-events.md)) | new |

Common rules: every mutating POST accepts `?dry_run=true` and returns the diff; bulk operations
are idempotent (same request twice = same state); errors in RFC 9457 format.
The diagnostic and fix routes (`diagnose_*`, `fix_*`, `debug_*`) **are not migrated**: they become
admin reports or read-only MCP tools.

### Backoffice

Owner decision 2026-11-09: there is **one management area, the Manager** (working path
`/manager/`), for vendor members and for platform staff, in place of a separate vendor dashboard
and a commerce admin. The idea comes from the legacy site's vendor store manager, which the owner
liked. Next to it, the Wagtail admin keeps CMS pages, images, snippets and editors, and the Django
admin stays technical-only.

| Area | Who | What |
|---|---|---|
| Manager, shop space `/manager/shop/` | Vendor owner and vendor staff | Their own vendor's work: dashboard, orders, quotes, products, events, payouts, documents, settings (CORE.9, CORE.17) |
| Manager, platform space `/manager/platform/` | Platform staff roles and the platform owner | Marketplace work across vendors, each role only its own sections (CORE.17, CORE.18) |
| Wagtail admin | Editors; event moderators | Pages, blog, images, snippets, translations, event page review (CORE.8, Q-58) |
| Django admin | Technical superuser | Maintenance only, from a restricted network (OPS.5, Q-18) |

#### The Manager (CORE.17)

The Manager is **one app** (`apps/manager/`, an adapter like `api`: views, templates and the
navigation registry, no business logic) with one shell: the same layout, components, look and
login for everyone. It has **two spaces**, split by URL prefix:

- **Shop space** `/manager/shop/`: vendor members only (owner or staff, CORE.2). Every page works
  on one vendor, the actor's selected vendor; a person who belongs to several vendors picks the
  vendor from the header, and the choice is checked against their memberships on every request.
  Every query goes through `VendorScopedQuerySet.for_actor` (CORE.3), so the tenant isolation
  rule stays where it is: in the services.
- **Platform space** `/manager/platform/`: platform staff roles only (CORE.18). Each role sees
  only its own sections.

Security rules:

- **One gate per prefix.** A middleware (or base view mixin) at each prefix lets in only the
  actors that space is for: vendor members for the shop space, holders of at least one platform
  staff role (or a superuser, for the impersonation policy only) for the platform space. A
  signed-in user without that right gets **404** (not 403,
  so the pages cannot be enumerated); an anonymous visitor goes to the login. A vendor member
  never reaches a platform section; a platform staff member who is not a vendor member reaches
  the shop space only through a support impersonation session (CORE.10, below).
- **Inside the platform space**, each section declares the permission it needs in the navigation
  registry; the menu shows only the sections the actor may open, and the section's views check
  the same permission (404 otherwise). The menu is never the security boundary.
- **Tested once per space plus a sweep.** The gate is tested once for each space, the matrix
  cells by CORE.18, and a sweep test walks every URL registered under `/manager/` and fails when
  one sits outside exactly one gated prefix, so a new view cannot be added ungated.
- **MFA for everyone** who enters the Manager (CORE.4), and an MFA step-up on sensitive actions:
  IBAN and payout changes, rate and configuration changes, role assignment, data exports, the
  start of an impersonation session.
- **Two kinds of role, one person.** A person who holds both a vendor membership and a platform
  role switches space from the header. Staff who correct a vendor's data in their normal work do
  it from the platform space through the same services, audited. Entering the shop space as a
  vendor user is a support impersonation session (CORE.10), never a side door.

What the shop space offers is the vendor dashboard of [04](04-vendors-commissions-ledger.md)
(orders, quote entry, products with limited editing per D-14, balance and payouts, documents, tax
regime change request, storefront profile and policies, holiday and closure schedule VEN.20,
events [15](15-events.md)). Other documents say "vendor area" for the shop space; its paths are
under `/manager/shop/`. Vendors never get the Wagtail admin: their event pages are created and
edited as revisions through the events services.

**What the legacy store manager taught us.** The legacy site's vendor store manager was one
front-end dashboard with a menu of sections: a sales dashboard, products and a media library,
orders, customers, coupons, refunds, payments and withdrawals, a ledger, reviews, followers,
enquiries and support tickets, reports, articles, shop staff, and settings (store profile,
policies, payment, a custom tax section added by a site plugin; the built-in shipping replaced by
a custom multi-carrier module, and a custom quote panel on the order page). The platform
administrator used the same dashboard with extra sections (vendor list, vendor groups, managers,
memberships) and a capability screen that switched sections on or off for vendors. The Manager
keeps the single dashboard with a section menu and the per-role sections; it drops what is not
used (coupons, followers, affiliate, membership billing, see [91](91-estimates.md)), and the
capability screen becomes the tested role matrix below instead of free toggles.

Navigation per space:

| Space | Sections (in menu order) |
|---|---|
| Shop | Dashboard; Orders and quotes; Products and media; Events; Payouts and balance (owner); Documents; Storefront profile and policies (owner); Settings: closures, members and API keys (owner), tax regime request (owner); Access by support (owner) |
| Platform | Dashboard; Configuration and tax; Catalog and taxonomies; Imports; Moderation; Vendors; Orders and customers; Support actions and impersonation; Finance and payouts; Tax and DAC7 reports; Staff and roles; Audit log |

#### Support impersonation (CORE.10)

Owner decisions 2026-11-09: platform staff can enter the shop space as a vendor user, to give
support, see what the user sees and fix what the user cannot find or understand; the limits are
configurable, inside a hard floor that nobody can configure.

- **Who.** The roles the impersonation policy allows (by default Support and Configuration and
  tax; never Finance or Catalog and moderation unless the policy says so). The subject is a vendor
  member; never another platform staff account or a superuser. A staff member cannot impersonate
  inside a vendor of which they are themselves a member.
- **Start.** From the platform space, on a vendor user, behind an MFA step-up, with a mandatory
  reason (free text, plus an optional ticket reference). Two modes: **view as** (read-only, the
  default; every POST, PUT, PATCH and DELETE is refused) and **act as** (changes allowed, after a
  second explicit confirmation).
- **Always on**, for every role and also with full access: a permanent banner that cannot be
  dismissed ("You are acting as <user> for <vendor>, staff <name>, ends in mm:ss, Exit"); a time
  limit (the policy's maximum, default 30 minutes, extendable once with a new step-up); the real
  staff identity on the request (`request.real_user`), with every `AuditLog` entry written during
  the session recording the actor (the staff member), the subject (the impersonated user) and the
  impersonation session id; the vendor's "Access by support" log.
- **After.** The vendor owner sees every session in "Access by support" in the shop space (who,
  when, why, which mode, whether full access was used, what changed) and gets an email (ARC.16)
  when an "act as" session made changes.

**Impersonation policy.** A page "Impersonation policy" in the platform space (configuration),
versioned and audited like the ARC.6 and ARC.7 settings (ARC.24). Only a superuser changes it,
not the Configuration and tax role, so nobody raises their own power; every change needs an MFA
step-up and a reason. Per platform role it sets: whether the role may start impersonation, which
modes, the maximum session length, and which **configurable action groups** are allowed in "act
as":

| Configurable group | Default in "act as" |
|---|---|
| Vendor member management (add, remove, change role) | off |
| Product deletion and bulk retire | off |
| Order actions (cancel, refund request) | off |
| Storefront profile and policies | on |
| Data exports | off |
| Notification settings | on |

Ordinary edits outside these groups (product texts, stock, quotes, closures) are allowed in "act
as". Deleting or closing a vendor is not a shop-space action at all: it is the platform owner's
vendor lifecycle, so no impersonation session reaches it.

**Full access.** A superuser can grant full access to a **named user** (never to a whole role),
behind an MFA step-up, with a mandatory reason and an expiry date (at most 90 days, renewable).
Grants are listed on the policy page. Full access allows every configurable group. Grants, their
expiry and every use are in the `AuditLog` and in the vendor's "Access by support" log.

**Hard floor.** Not configurable by anyone, enforced in code by a decorator or permission on the
service or view (hiding a button is not enough), each item tested, also with full access. While
impersonating, never:

| Refused action | Why | Proper staff flow in the platform space |
|---|---|---|
| The user's password, email, MFA factors, passkeys or recovery codes | Account-takeover vector | The audited reset flow (Support actions) |
| IBAN, payout account or payment provider onboarding | Payout-fraud vector | The audited staff change with verification (VEN.12; provider onboarding stays with the vendor, VEN.6) |
| Accepting contracts or legal and tax attestations in the vendor's name (DAC7 confirmations, regime change requests) | Not legally valid when done by someone else | The audited staff correction (MQ-6, MQ-1) |
| Creating or revealing API keys | A key outlives the session and acts without the banner | None: the vendor owner issues keys |

The refusal page names the proper staff flow. The policy page shows these four as fixed, with
the reason.

#### Role-permission matrix (CORE.18)

Owner decision 2026-11-09. Platform staff work through four roles, plus the existing platform
owner, editors and technical superuser. Each role is a group holding dedicated permissions
(`manager.platform_config`, `manager.platform_catalog`, `manager.platform_finance`,
`manager.platform_support`); none is implied by `is_staff`, Wagtail access or another role. The
platform-wide read of vendor data (`vendors.view_all_vendors`, Q-34) belongs to the roles that
need it (catalog, finance, support, platform owner); the configuration role sees configuration
objects only, and a vendor-specific commission override shows the vendor's name and plan, no
orders, customers or bank data.

Legend: **E** edit (with dry-run and audit), **R** read only, **A** audited support actions, **I**
support impersonation within the impersonation policy (CORE.10), **—** no access (404). The
technical superuser enters the platform space only for the impersonation policy.

| Section | Vendor owner | Vendor staff | Config. and tax | Catalog and moderation | Finance and payments | Support | Platform owner | Editor | Tech. superuser |
|---|---|---|---|---|---|---|---|---|---|
| Shop: dashboard, orders, quotes, products, events, closures (own vendor) | E | E | I (policy) | — | — | I (policy) | — | — | — |
| Shop: payouts, IBAN, tax regime request, storefront profile, members, API keys (own vendor) | E | — | I (policy, never the hard floor) | — | — | I (policy, never the hard floor) | — | — | — |
| Shop: access by support log (own vendor) | R | — | — | — | — | — | — | — | — |
| Platform: configuration and tax (ARC.24) | — | — | E | — | — | — | R | — | — |
| Platform: owner-only settings (CMP.13, CMP.11 approvals) | — | — | R | — | — | — | E | — | — |
| Platform: catalog, categories, attributes, taxonomies, imports | — | — | — | E | — | R | R | — | — |
| Platform: moderation (vendor events, storefront changes, content) | — | — | — | E | — | — | R | — | — |
| Platform: vendors (applications, status, plans) | — | — | R (name, plan) | R | R | R | E | — | — |
| Platform: orders and customers | — | — | — | — | R | R + A | R | — | — |
| Platform: support actions (unlock, resend link) | — | — | — | — | — | A | A | — | — |
| Platform: start an impersonation session (CORE.10; defaults, the policy decides) | — | — | A | — | — | A | — | — | — |
| Platform: impersonation policy and full-access grants (CORE.10) | — | — | R | — | — | R | R | — | E |
| Platform: finance (payouts, balances, refunds, reconciliation) | — | — | — | — | E | — | R | — | — |
| Platform: tax and DAC7 reports | — | — | — | — | E | — | R | — | — |
| Platform: staff and roles | — | — | — | — | — | — | E | — | — |
| Platform: audit log (SEC.4) | — | — | own sections | own sections | own sections | own sections | R | — | — |
| Wagtail admin: pages, images, snippets | — | — | — | event review only | — | — | — | E (own subtree) | — |
| Django admin | — | — | — | — | — | — | — | — | E |

Rules:

- The matrix is data in one place (`apps/manager/roles.py`); the menu, the section checks and the
  tests read it. A parametric test checks every cell: an allowed cell answers 200 (and an edit
  cell accepts a dry-run POST), a forbidden cell answers 404 on GET and POST and changes nothing.
- The platform owner assigns platform staff roles from "Staff and roles", behind an MFA step-up,
  with an `AuditLog` entry; the technical superuser can do the same from the Django admin.
- Separation of duties: Finance changes no configuration, Configuration moves no money, Support
  edits no product, payout or configuration from the platform space.

#### Wagtail and Django admin

- **Wagtail admin** for editors: pages, images, snippets, translations. Editors get our own
  roles, each limited to its part of the page tree (for example blog editors, legal pages, event
  moderators), instead of Wagtail's default Editors and Moderators groups (owner, 2026-11-07,
  Q-58; CORE.8). It holds no commerce screens: vendors, products, orders, rates and payouts are
  in the Manager platform space.
- **Django admin** only for the technical superuser, reachable from a restricted network (OPS.5,
  owner decision Q-18). Staff work in the Manager (CORE.17). Until CORE.8 the rule is strict
  (owner decision MQ-7): only a superuser manages users and groups; a staff member without the
  superuser flag cannot grant staff or superuser status, groups or permissions, cannot set a
  staff password or reset a staff member's MFA, and cannot open the group admin.
- **MCP server** (see [10](10-content-seo-ai.md)): tools on top of the same services.

### Vendor membership, storefront profile and erasure (owner decision Q-16)

**The owner stays.** A vendor owner is a contractual and business party of the platform, not a
newsletter subscriber. Every vendor keeps at least one owner, also once it is closed: removing or
demoting the last owner is refused in every vendor status. `check_keeps_an_owner` in
`apps/vendors/engine.py`, called by `add_member`, `change_member_role` and `remove_member` in
`apps/vendors/services.py`, already enforces this for every status (CORE.2), so no code change is
needed.

**Erasure request from a closed vendor's owner.** Legal obligations prevail (GDPR article
17(3)(b), and 17(3)(e) for the contract evidence, [11](11-compliance.md)):

- the request restricts processing of the owner's data (GDPR article 18): the account is
  deactivated, the data are used only to meet the retention duties or to establish and defend
  legal claims, and the membership row stays;
- the data covered by a retention duty stay for their retention period: contract and signature
  evidence (VEN.14, VEN.15), tax and DAC7 records (CMP.1, CMP.12), ledger entries (VEN.4) and
  order records (ORD.2);
- every other personal datum is deleted or anonymised at once (for example the phone number,
  the preferred language, MFA devices and sessions);
- the CMP.4 retention job anonymises the retained data when the last retention period ends.

The flow is part of the CMP.4 erasure task. An active vendor's owner who asks for erasure must
first hand the ownership to another person or close the vendor.

**Storefront profile.** Only the vendor owner edits the storefront profile: texts, logo, return,
shipping and cancellation policies (`VendorTranslation` and the logo). Vendor staff cannot. The
policies carry legal weight towards customers. A change is published at once and notifies the
moderators (post-moderation). Every change writes an `AuditLog` entry with before and after, and
staff (catalog and moderation role) can revert a change to an earlier version from the
platform space. This is part of CORE.9.

## 2.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CORE.1 | Custom `User`, login via email, preferred language | P1 | 6 | ARC.6 | Initial migration with custom User |
| CORE.2 | `VendorMember` with owner/staff roles, per-role permissions | P1 | 6 | CORE.1, VEN.1 | Role matrix tested |
| CORE.3 | `VendorScopedQuerySet` + 404 helper + reusable cross-tenant test fixture | P1 | 8 | CORE.2 | Negative tests generated for every endpoint |
| CORE.4 | MFA (TOTP + passkey) mandatory for staff and every vendor member, recovery codes, step-up | P1 | 12 | CORE.1 | Login without MFA impossible for required roles |
| CORE.5 | Rate limiting and progressive lockout on login/registration, Turnstile | P1 | 6 | CORE.1 | Tests on failed attempts |
| CORE.6 | `ApiClient`: issuance, hashing, scopes, expiry, rotation, revocation, IP allow-list | P1 | 10 | CORE.1 | Expired or out-of-scope key → 401/403 |
| CORE.7 | Ninja API v1 skeleton: auth, dry-run, RFC 9457 errors, OpenAPI, mutation log | P1 | 10 | CORE.6, ARC.13 | OpenAPI spec published on STAGE |
| CORE.8 | Wagtail admin for CMS pages, images, snippets and editors only (owner decision 2026-11-09: staff commerce work, with the base list views, filters and bulk actions, moves to the Manager platform space, CORE.17); platform staff as a dedicated permission instead of `is_staff` in `for_actor` (Q-34); account unlock service with audit, used by the Support role from the Manager (CORE.18), and automatic unlock after a completed password reset (Q-18); our own editor roles (for example blog editors, legal pages, event moderators) replacing Wagtail's default Editors and Moderators groups, which stay until then (Q-58) | P1 | 12 | CORE.3 | A blog editor cannot edit the About page or a legal page; Wagtail's default Editors and Moderators groups no longer exist after the data migration; a user with Wagtail admin access but without the platform staff permission sees no vendor's data (negative test); the Wagtail admin menu has no vendor, product, order, rate or payout item; the unlock service clears a lockout and writes the AuditLog entry; a completed password reset clears the lockout |
| CORE.9 | Vendor area, now the shop space `/manager/shop/` of the Manager (owner decision 2026-11-09; it was a separate `/vendor/` area; shell, layout and navigation come from CORE.17): essential KPI dashboard; storefront profile editing (texts, logo, return and shipping policies) for the owner only, with post-moderation notice to the moderators, audit and staff revert from the platform space (Q-16); new applicants arrive from the VEN.22 public application | P1 | 13 | CORE.3, CORE.17 | Vendor sees only their own data; vendor staff cannot edit the storefront profile; an owner's profile change notifies the moderators, is in the AuditLog with before and after, and staff revert it to the previous version |
| CORE.10 | Support impersonation into the Manager shop space (owner decisions 2026-11-09, moved from P2 to P1; spec 02 §Backoffice): by default the Support and Configuration and tax roles only, never into a platform staff or superuser account nor into a vendor the staff member belongs to; started from the platform space with MFA step-up and a mandatory reason (free text, optional ticket reference); modes "view as" (read-only, default) and "act as" (second explicit confirmation); always on, full access included: non-dismissable banner with subject, vendor, staff name, countdown and Exit, time limit (policy maximum, default 30 min, extendable once with step-up), `request.real_user` and every AuditLog entry of the session with actor, subject and session id, the vendor owner's "Access by support" log and an ARC.16 email after an "act as" change; versioned "Impersonation policy" page changed only by a superuser with step-up and reason: per role, whether it may start, which modes, maximum length and which configurable "act as" groups are allowed (member management, product deletion and bulk retire, order actions, storefront profile, data exports, notification settings); full-access grants by a superuser to a named user only, with step-up, reason and expiry (at most 90 days, renewable), listed on the page; hard floor refused server-side by a decorator or permission, never configurable, each refusal pointing to the proper staff flow: password, email, MFA factors, passkeys and recovery codes; IBAN, payout account and payment provider onboarding; contracts and legal or tax attestations in the vendor's name; API key creation or reveal | P1 | 14 | CORE.17, CORE.18, CORE.4, ARC.24 | A Support user viewing as a vendor member sees exactly that member's shop space and every write is refused in "view as"; an "act as" product edit is in the AuditLog with staff, subject and session id; Finance and Catalog and moderation users cannot start a session under the default policy (404); a session without step-up or without a reason is refused; impersonating a staff account, a superuser or a vendor the staff member belongs to is refused; the session ends at the policy maximum (30 min by default) and the second extension is refused; a policy change by a non-superuser, the Configuration role included, is refused; a disabled configurable group is refused in "act as"; a full-access grant expires on its date; with full access a member can be removed while impersonating and the AuditLog entry names staff and subject; each hard-floor action is refused in both modes, also with full access, one test per action; the vendor owner sees the session in "Access by support" and gets the email after an "act as" change |
| CORE.12 | Per-API-key rate limit (Q-18): limits per `ApiClient` from settings (default `120/minute` per key and per environment, staff-adjustable per key from CORE.16, owner decision MQ-4), counters in a cache shared by every app process (PostgreSQL-backed, spec 01), 429 response in RFC 9457 format with `Retry-After` | P1 | 4 | CORE.7 | Two app processes share one counter; the request over the limit gets 429 with `Retry-After`; another key is not affected |
| CORE.15 | Secure action links: signed token bound to object ID, recipient and version, configurable expiry (72 h default), single-use sign-in consumed only by the POST of an intermediate "Continue" page, magic sign-in without password but MFA step-up before the action POST, misuse to normal login with audit; service in `accounts` and email helper on ARC.16 | P1 | 6 | CORE.4, ARC.16 | A GET or HEAD neither consumes the token nor changes state; a valid token without a session leads to the step-up and then to the page; a token for user X cannot open user Y's object; an old-version token is refused; an action without step-up is refused |
| CORE.16 | API rate limit defaults (owner decision MQ-4): `API_RATE_LIMIT` default lowered from `600/minute` to `120/minute`, configured per environment; a per-key override stored on the `ApiClient` that staff set from the admin (raise or lower), behind an MFA step-up and in the AuditLog, winning over `API_RATE_LIMIT_CLIENTS` | P1 | 3 | CORE.12, CORE.4 | Without configuration a key gets 429 on its 121st call in a minute; an environment setting changes the default; a staff override raises one key's limit without affecting another key; the override change is refused without step-up and is in the AuditLog |
| CORE.13 | `ApiClient.vendor`: optional vendor FK on API keys (empty = platform key), vendor-scoped API keys enforced through CORE.3 scoping, admin and tests | P1 | 3 | CORE.3, CORE.6 | A vendor key cannot read or change another vendor's data (negative tests) |
| CORE.14 | Vendor API key policy (coherence check 5): a vendor-grantable scope set apart from platform-only scopes, keys of a suspended vendor limited to reads and keys of a closed vendor refused, one AuditLog entry per key when a vendor and its keys are deleted; needed before EVT.5 exposes vendor event scopes | P1 | 3 | CORE.13 | A vendor key cannot be issued with a platform-only scope; a suspended vendor's key gets 403 on writes and succeeds on reads; a closed vendor's key gets 401; deleting a vendor writes one audit entry per key |
| CORE.17 | Manager shell (owner decision 2026-11-09, spec 02 §Backoffice): one adapter app `apps/manager/` (views, templates, navigation registry, no business logic), one layout, component set and login; two spaces by URL prefix, shop `/manager/shop/` for vendor members (vendor picker for people in several vendors, the choice re-checked against memberships on every request, all data through CORE.3 `for_actor`) and platform `/manager/platform/` for platform staff roles; one gate per prefix (middleware or base mixin) answering 404 to a signed-in user without the space's right and sending anonymous visitors to the login; navigation registry where each section declares its space and permission, the menu showing only the sections the actor may open and each view checking the same permission; space switch in the header for people who hold both kinds of role; base list view with filters, pagination and bulk actions with dry-run; MFA (CORE.4) for every Manager user and step-up on sensitive actions; the vendor-area pages of CORE.9, VEN.9, VEN.15, VEN.20, EVT.4, ORD.6 and CAT.18 plug into the shop space | P1 | 18 | CORE.3, CORE.4 | A vendor member opens `/manager/shop/` and gets 404 on every `/manager/platform/` URL (GET and POST); a platform staff member without a vendor membership gets 404 on `/manager/shop/` outside an impersonation session; a customer gets 404 on both spaces and an anonymous visitor is sent to the login; a member of two vendors switches vendor and never sees the other vendor's rows (CORE.3 fixture), and a forged vendor id in the session or the request gives 404; a user without a confirmed second factor is sent to enrolment before any Manager page; a sweep test fails when a URL registered under `/manager/` is not under exactly one gated prefix; a section hidden from the menu still answers 404 when its URL is typed |
| CORE.18 | Platform staff roles and the role-permission matrix (owner decision 2026-11-09, spec 02 §Role-permission matrix): groups and dedicated permissions for Configuration and tax, Catalog and moderation, Finance and payments and Support next to the platform owner, editors and technical superuser; `vendors.view_all_vendors` (Q-34) only for the roles that need it; the matrix as data in `apps/manager/roles.py` read by the menu, the section checks and a parametric test of every cell; role assignment by the platform owner in "Staff and roles" with MFA step-up and AuditLog, and by the technical superuser in the Django admin; the Support actions (unlock an account with CORE.8, resend a CORE.15 link or a VEN.24 claim) as audited buttons | P1 | 10 | CORE.8, CORE.17 | Every matrix cell is tested: allowed answers 200, forbidden answers 404 on GET and POST and leaves the data unchanged; a Finance user's POST to a configuration page is 404 and changes nothing; a Configuration user sees no order, customer or bank data; a Catalog user sees no payout or balance; a Support user unlocks an account (in the AuditLog) and gets 404 on product, payout and configuration edits; a vendor member gets 404 on every platform section; an editor without a platform role gets 404 on the platform space; a role change without step-up is refused, and a granted role is in the AuditLog with before and after |
