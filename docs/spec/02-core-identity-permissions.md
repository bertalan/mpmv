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
    # owner: payouts, IBAN, VAT regime, users; staff: orders, quotes, products


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
| Staff operator | Wagtail admin + commerce admin | Vendors, catalog, orders, quotes, zones, rates |
| Technical superuser | Django admin (restricted network) | Maintenance; exceptional, tracked use |

### Tenant (vendor) isolation

A single mechanism, used everywhere (views, API, MCP, exports, admin search):

```python
class VendorScopedQuerySet(QuerySet):
    def for_actor(self, actor: Actor) -> "VendorScopedQuerySet":
        """Staff: everything. Vendor member: only their own records. Others: nothing."""

def get_for_actor_or_404(qs, actor, **lookup):  # 404 (not 403) to prevent enumeration
```

- Every model with a `vendor` FK uses this manager.
- **Mandatory negative tests** for every endpoint: "Vendor A requests Vendor B's object → 404".
- Vendor documents (KYC, invoices) can be downloaded only from views that check permissions; never
  public URLs.

### Authentication

- Web session: modern password handling (check against compromised passwords), **mandatory TOTP/passkey
  MFA** for staff and vendor owners, progressive lockout per account and IP.
- Step-up (re-authentication) for: IBAN change, payout, rate changes, customer data export.
- API: `Authorization: Bearer mpmv_<prefix>_<secret>` header, constant-time comparison,
  scope checked per route, rate limit per client, logging of every mutating call.
- Impersonation for support (equivalent of Login as User), staff only, with audit.

### API v1 (replaces `api.php`)

The ~85 "recurring" routes are consolidated into ~15 REST resources:

| Resource | Operations | Replaces |
|---|---|---|
| `/products` | bulk upsert by `(vendor, sku)`, get, paginated export, delisting | `create_product_full`, `update_product_*_relationships`, `get_all_product_*`, `export_vendor_products`, `delete_product`, `trash_products` |
| `/products/{sku}/variants` | variant upsert, axes, default | `convert_to_variable_product` |
| `/stock`, `/prices` | bulk update with thresholds | `batch_update_stock`, `get_vendor_product_stock/prices` |
| `/media` | direct upload (presigned URL), cover/gallery assignment | `upload_media`, `upload_images_from_folder`, `convert_images_webp`, `regenerate_product_thumbnails` |
| `/taxonomy/{categories,taxonomies,terms,brands}` | CRUD with translations and SEO copy | `create_categories`, `create_tags`, `*_tag_translations`, `update_*_descriptions`, `assign_*` |
| `/taxonomy/assign-by-content` | keyword-based assignment with dry-run | `assign_brand_tags_by_content` |
| `/shipping/config`, `/shipping/simulate` | validated read/write, simulation | `*_custom_shipping_vendors_config*`, `manage_gls_shipping_zones`, `test` |
| `/tax/rates`, `/tax/simulate` | rates with validity, cart simulation | `sync_eu_standard_vat_rates`, `simulate_cart_tax` |
| `/vendors/{id}` | profile, policies, logo (per language) | `update_vendor_*` |
| `/reviews` | supplier review import | `manage_product_reviews` |
| `/content/posts` | blog drafts | `create_post`, `update_post` |

Common rules: every mutating POST accepts `?dry_run=true` and returns the diff; bulk operations
are idempotent (same request twice = same state); errors in RFC 9457 format.
The diagnostic and fix routes (`diagnose_*`, `fix_*`, `debug_*`) **are not migrated**: they become
admin reports or read-only MCP tools.

### Backoffice

- **Wagtail admin** for staff and editors: pages, images, snippets; `ModelViewSet` for vendors,
  products, zones, rates, orders and quotes, with filters and bulk actions.
- **Vendor dashboard** as a separate area (`/vendor/`), HTMX templates, tenant-filtered views:
  orders, quote entry, products (limited editing), payouts, tax regime change
  request.
- **Django admin** only for the technical superuser, reachable from a restricted network.
- **MCP server** (see [10](10-content-seo-ai.md)): tools on top of the same services.

## 2.3 Tasks

| ID | Micro-task | Pri | Hours | Depends on | Done when |
|---|---|---|---|---|---|
| CORE.1 | Custom `User`, login via email, preferred language | P1 | 6 | ARC.6 | Initial migration with custom User |
| CORE.2 | `VendorMember` with owner/staff roles, per-role permissions | P1 | 6 | CORE.1, VEN.1 | Role matrix tested |
| CORE.3 | `VendorScopedQuerySet` + 404 helper + reusable cross-tenant test fixture | P1 | 8 | CORE.2 | Negative tests generated for every endpoint |
| CORE.4 | MFA (TOTP + passkey) mandatory for staff/owners, recovery codes, step-up | P1 | 12 | CORE.1 | Login without MFA impossible for required roles |
| CORE.5 | Rate limiting and progressive lockout on login/registration, Turnstile | P1 | 6 | CORE.1 | Tests on failed attempts |
| CORE.6 | `ApiClient`: issuance, hashing, scopes, expiry, rotation, revocation, IP allow-list | P1 | 10 | CORE.1 | Expired or out-of-scope key → 401/403 |
| CORE.7 | Ninja API v1 skeleton: auth, dry-run, RFC 9457 errors, OpenAPI, mutation log | P1 | 10 | CORE.6, ARC.13 | OpenAPI spec published on STAGE |
| CORE.8 | Wagtail admin: staff menu, base ViewSet with tenant filters and bulk actions | P1 | 10 | CORE.3 | Staff manage vendors and products from the admin |
| CORE.9 | Vendor area `/vendor/`: layout, navigation, essential KPI dashboard | P1 | 12 | CORE.3 | Vendor sees only their own data |
| CORE.10 | Support impersonation with audit | P2 | 4 | CORE.8 | Every impersonated session in AuditLog |
| CORE.13 | `ApiClient.vendor`: optional vendor FK on API keys (empty = platform key), vendor-scoped API keys enforced through CORE.3 scoping, admin and tests | P1 | 3 | CORE.3, CORE.6 | A vendor key cannot read or change another vendor's data (negative tests) |
