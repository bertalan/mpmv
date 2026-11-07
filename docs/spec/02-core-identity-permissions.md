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
| Vendor owner | Vendor dashboard | Everything concerning **their own** vendor, including financial settings |
| Vendor staff | Vendor dashboard | Orders, quotes, products; no payouts, IBAN or tax regime |
| Editor | Wagtail admin | Pages, blog, SEO copy, translations |
| Staff operator | Wagtail admin + commerce admin | Vendors, catalog, orders, quotes, zones, rates. Seeing every vendor's data needs the dedicated platform staff permission (owner decision Q-34); Wagtail or Django admin access alone is not enough |
| Platform owner | Commerce admin | Staff operator rights plus the owner-only settings: allowed vendor establishment countries (CMP.13) and approval of staff proposals to turn EPR checks on or off per country (CMP.11). A dedicated permission, never implied by staff or admin access; it never approves its own proposal |
| Technical superuser | Django admin (restricted network) | Maintenance; exceptional, tracked use |

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
  example `vendors.view_all_vendors`, given only to the operations staff group. Access to the
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
- Unlocking (owner decision Q-18): staff can unlock a locked account from the admin, with an
  `AuditLog` entry, and a completed password reset clears the account's lockout (CORE.8).
- Step-up (re-authentication) for: IBAN change, payout, rate changes, customer data export.

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
- Impersonation for support (equivalent of Login as User), staff only, with audit.

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

- **Wagtail admin** for staff and editors: pages, images, snippets; `ModelViewSet` for vendors,
  products, zones, rates, orders and quotes, with filters and bulk actions. Editors get our own
  roles, each limited to its part of the page tree (for example blog editors, legal pages, event
  moderators), instead of Wagtail's default Editors and Moderators groups (owner, 2026-11-07,
  Q-58; CORE.8).
- **Vendor dashboard** as a separate area (`/vendor/`), HTMX templates, tenant-filtered views:
  orders, quote entry, products (limited editing), payouts, tax regime change
  request, holiday and closure schedule (VEN.20), events. Vendors never get the Wagtail admin: their event pages are created and edited
  as revisions through the events services ([15](15-events.md)).
- **Django admin** only for the technical superuser, reachable from a restricted network (OPS.5,
  owner decision Q-18). Staff work in the Wagtail admin (CORE.8).
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
staff can revert a change to an earlier version. This is part of CORE.9.

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
| CORE.8 | Wagtail admin: staff menu, base ViewSet with tenant filters and bulk actions; platform staff as a dedicated permission instead of `is_staff` in `for_actor` (Q-34); staff unlock of a locked account with audit, and automatic unlock after a completed password reset (Q-18); our own editor roles (for example blog editors, legal pages, event moderators) replacing Wagtail's default Editors and Moderators groups, which stay until then (Q-58) | P1 | 15 | CORE.3 | Staff manage vendors and products from the admin; a blog editor cannot edit the About page or a legal page; Wagtail's default Editors and Moderators groups no longer exist after the data migration; a user with Wagtail admin access but without the platform staff permission sees no vendor's data (negative test); staff unlock a locked account and the unlock is in the AuditLog; a completed password reset clears the lockout |
| CORE.9 | Vendor area `/vendor/`: layout, navigation, essential KPI dashboard; storefront profile editing (texts, logo, return and shipping policies) for the owner only, with post-moderation notice to the moderators, audit and staff revert (Q-16); new applicants arrive from the VEN.22 public application | P1 | 16 | CORE.3 | Vendor sees only their own data; vendor staff cannot edit the storefront profile; an owner's profile change notifies the moderators, is in the AuditLog with before and after, and staff revert it to the previous version |
| CORE.10 | Support impersonation with audit | P2 | 4 | CORE.8 | Every impersonated session in AuditLog |
| CORE.12 | Per-API-key rate limit (Q-18): limits per `ApiClient` from settings, counters in a cache shared by every app process (PostgreSQL-backed, spec 01), 429 response in RFC 9457 format with `Retry-After` | P1 | 4 | CORE.7 | Two app processes share one counter; the request over the limit gets 429 with `Retry-After`; another key is not affected |
| CORE.15 | Secure action links: signed token bound to object ID, recipient and version, configurable expiry (72 h default), single-use sign-in consumed only by the POST of an intermediate "Continue" page, magic sign-in without password but MFA step-up before the action POST, misuse to normal login with audit; service in `accounts` and email helper on ARC.16 | P1 | 6 | CORE.4, ARC.16 | A GET or HEAD neither consumes the token nor changes state; a valid token without a session leads to the step-up and then to the page; a token for user X cannot open user Y's object; an old-version token is refused; an action without step-up is refused |
| CORE.13 | `ApiClient.vendor`: optional vendor FK on API keys (empty = platform key), vendor-scoped API keys enforced through CORE.3 scoping, admin and tests | P1 | 3 | CORE.3, CORE.6 | A vendor key cannot read or change another vendor's data (negative tests) |
| CORE.14 | Vendor API key policy (coherence check 5): a vendor-grantable scope set apart from platform-only scopes, keys of a suspended vendor limited to reads and keys of a closed vendor refused, one AuditLog entry per key when a vendor and its keys are deleted; needed before EVT.5 exposes vendor event scopes | P1 | 3 | CORE.13 | A vendor key cannot be issued with a platform-only scope; a suspended vendor's key gets 403 on writes and succeeds on reads; a closed vendor's key gets 401; deleting a vendor writes one audit entry per key |
