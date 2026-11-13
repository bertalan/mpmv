# Audit trail

Every sensitive mutation writes an append-only `AuditLog` entry: who, what, before/after and
from where (spec 01 conventions, spec 12 §12.3 requirement SEC.7, backlog tasks ARC.6 and SEC.4). This page lists what
is audited today, the rules new code must follow, and how rate models (TAX, SHP, VEN
commissions) hook in.

## Reading the log

Staff open *Core → Audit logs* in the admin (`/django-admin/core/auditlog/`). The view is
read-only (no add, change, delete or bulk actions; the database refuses updates and deletes
anyway) and asks for a fresh second factor: it is wrapped in the CORE.4 step-up named by
`settings.STEP_UP_DECORATOR`.

Filters, which combine:

| Filter | Query parameter | Matches |
|---|---|---|
| Actor | `actor` | `actor_label` contains the text (`staff:alice@`, `anonymous`, `api:`...) |
| Action | `action` | one action, e.g. `vendor.updated` |
| Object type | `target_model` | one model label, e.g. `core.platformoperator` |
| Source | `source__exact` | admin, vendor, api, command, task, mcp, system |
| Date range | `created_from`, `created_to` | creation date between the two days (inclusive, `YYYY-MM-DD`) |
| Vendor | `vendor` | entries about one vendor (its public id) |

The detail page shows a *Changes* table (field, before, after) above the raw snapshots. A
vendor's change page links to its trail (`?vendor=<public id>`).

## What is audited

| Area | Actions |
|---|---|
| Logins (CORE.5) | `accounts.login.succeeded`, `accounts.login.failed`, `accounts.login.locked_out`, `accounts.registration.rate_limited`; `accounts.user.unlocked` (platform staff lift a lockout), `accounts.user.unlocked_by_reset` (a completed password reset clears it) |
| Second factors (CORE.4) | `accounts.mfa.enrolled`, `.verified`, `.verification_failed`, `.recovery_code_used`, `.recovery_codes_generated`, `.device_removed`, `.disabled` |
| Passkey window (CORE.25, D-32) | `accounts.session.window_started` (kind `session`, the timers in force), `.window_ended` (reason: idle, cap, manual, logout; rows written before the wording change are `accounts.session.operational_started` and `.operational_ended`, still read by the friction report), `.single_granted` and `.single_used` (kind `single`: "Do it once", the action key, never the parameters), `.prompt` (every refusal or per-action passkey prompt: action, group, outcome; the friction report counts these) |
| Secure action links (CORE.15) | `accounts.action_link.sent` (the link's purpose, object and version, never the token), `.opened`, `.signed_in`, `.used`, `.refused` (refusals against a known account: expired, old version, wrong user; refusals with no account to attach them to, such as a bad signature, are only logged, without the token) |
| Users and permissions (admin) | `accounts.user.created`, `.updated`, `.deleted`, `.password_changed`, `.access_changed`; `auth.group.created`, `.updated`, `.deleted`, `.access_changed` |
| API keys (CORE.6) | `api.client.issued`, `.rotated`, `.revoked`; closing a vendor writes one `api.client.revoked` entry per key it revokes, and so does a reopen for a key a closure left active (CORE.14, through `settings.VENDOR_CLOSURE_HOOKS`); deleting a vendor writes one `vendor.deleted` entry per key it drops (CORE.14, through `settings.VENDOR_DELETION_AUDIT_HOOKS`) |
| API rate limits (CORE.16) | `api.client.rate_limit_changed` (one key's override, before and after; the admin asks for step-up first). Refused-key counters of CORE.20 (`api.apirefusedkey`) are not audited: each block writes one WARNING log line instead |
| Plans and commissions (VEN.2) | `vendor.plan_saved`; `vendor.commission_rule_added` (a new immutable version; closing the previous one is in the same entry set; the admin add page asks for step-up) |
| Vendors (VEN.1, CORE.2, VEN.12) | `vendor.created`, `.updated` (legal identity included, IBAN and tax code masked: they are encrypted at rest, SEC.3), `.deleted`, `.status_changed`, `.reopened` (with the reason, VEN.21), `.vat_checked`, `.member_added`, `.member_role_changed`, `.member_removed`; `vendor.legal_requirement_saved`, `.legal_requirement_deleted`; `vendor.no_tin_country_added`, `.no_tin_country_removed` (VEN.27: each with its reason; a removal sets `valid_to` and writes `removal_reason` and `vendors_without_tin` in `after`; the row is kept) |
| Storefront profile (CORE.9) | `vendor.profile_changed` (the owner's change to one language's storefront texts and policies, through `vendors.services.save_storefront_profile`; target `vendors.vendortranslation`, key `<vendor public id>/<language>`, source `vendor`). `before` and `after` hold the status and only the fields that changed, so an entry stays small (texts are capped at 10,000 characters for the description and 5,000 per policy) and staff can revert from it; the tagline is plain text (HTML refused) and the description and policies are cleaned rich text (VEN.29), stored as HTML. `vendor.profile_translation_stale` (one per other language whose reviewed or published text goes back to review because the source-language text changed: status before and after, same key format). The change is live at once; the moderators get a `vendors.profile_changed` outbox email (to the platform support address until the CORE.18 roles exist). A save that changes nothing writes no entry and sends no email; saves are limited per owner per hour (`VENDOR_PROFILE_SAVE_LIMIT`). Staff with `vendors.revert_storefront_profile` see the history in the Manager platform space and revert a change through `revert_profile_change` (required reason): `vendor.profile_reverted` (same target and key, source `admin`; `before` and `after` hold the status and the restored fields, `after` also `reason` and `reverts`, the public id of the reverted entry), plus a `vendors.profile_reverted` email to each vendor owner with the reason. A revert puts the old text back through the same checks as a save (rich text allow-list, caps, own-collection media rule), and the reason is free text typed by staff (plain text, HTML refused, at most 1,000 characters; the form tells staff not to write personal data in it) kept in `after.reason` and sent to the owners. Logo changes are reverted the same way through `revert_logo_change`: `vendor.logo_reverted` (target `vendors.vendor`, `after` holds `logo`, `logo_alt`, `reason`, `reverts`); the previous logo file is kept when the owner replaces or removes the logo (`before.logo` names it), so a revert restores the image and its alt text and deletes the one it replaces. The file is deleted when staff confirm the change (`vendor.logo_confirmed`, `after.entry`, `after.outcome`) or when the review window `VENDOR_LOGO_REVIEW_DAYS` closes (daily job `vendors.purge_retained_logos`, `vendor.logo_purged` with the outcome `deleted`, `gone` or `in_use`); after that a revert can only take the logo down. The history page lists both kinds |
| Vendor logo (CORE.9) | `vendor.logo_changed` (the owner sets, replaces or removes the logo, or changes its alt text, through `vendors.services.set_vendor_logo` and `remove_vendor_logo`; target `vendors.vendor`, key the vendor's public id, source `vendor`). `before` and `after` hold `logo` (`image:<id>`, or null) and `logo_alt`. The image goes through the SEC.2 upload checks into the vendor's own Wagtail collection `vendor-<public id>`, as own work of the vendor; a replaced or removed image is kept for the review window (see the storefront profile row), and an alt-text-only edit keeps no file (`before.logo` equals `after.logo`). Logo changes count against the same hourly save limits as the profile texts and notify the moderators like them |
| Storefront images and documents (VEN.31) | `vendor.media_uploaded` (the owner uploads an image or a PDF document from the storefront editor through `vendors.services.upload_storefront_image` or `upload_storefront_document`; `after.document` as `document:<id>` for a document; target `vendors.vendor`, key the vendor's public id, source `vendor`; `after` holds `image` as `image:<id>` and the title; the file goes through the SEC.2 checks into the vendor's own collection, counted against an hourly limit per owner). `vendor.media_denied` (a chooser or upload request named another collection or an image outside the vendor's own; the request answers 404; `after` holds the kind of request and the target; written at most 20 times per vendor and hour) |
| Vendor ledger (VEN.4) | `ledger.entry_recorded` (every `LedgerEntry`: vendor, kind, signed amount, currency, order, references, idempotency key; a repeated key with the same content writes nothing). The entry rows are append-only: the database refuses UPDATE and DELETE, and a mistake is corrected by a new `adjustment` entry |
| Vendor onboarding (VEN.5) | `vendor.application_submitted` (the applicant vendor and its owner membership), `vendor.contract_saved` (contract versions), `vendor.contract_accepted` (who, which version, language, text hash, IP; the acceptance rows are append-only too, and once a version is accepted the database refuses any change to it or its texts); `vendor.self_certified` (CMP.25: an owner certifies the DSA article 30(1)(e) statement; the entry records the text version, language, text hash and onboarding round, and certifying again in the same round writes nothing) |
| Vendor claim (VEN.24) | `vendor.claim_link_sent`, `.claim_link_regenerated`, `.claim_link_invalidated` (the old link, with the reason: regenerated or rejected); `vendor.claim_identity_confirmed`, `.claim_identity_refused` (never the typed VAT number or TIN), `.claim_locked` (too many wrong answers); `vendor.claim_submitted`, `.claim_approved`, `.claim_rejected`. Send, approve and reject ask for staff step-up |
| Public vendor application (VEN.22) | `vendor.application_submitted` (the new applicant vendor and its owner membership, one entry each; no password is taken at this step), `vendor.application_pending` (the pending marker, public ids and link version), `vendor.application_link_rotated` (a re-application for a pending address raised the version, so the older link died), `vendor.application_confirmed` (the account goes from inactive to active once the emailed link is used and the applicant has chosen a password; before and after `is_active`, never the password), `vendor.application_purged` (the hourly retention job, 3 days after the request: counts and the retention period only, no name or address), `vendor.application_reminded` (the one reminder at 48 h, the marker's public id), `vendor.application_cancelled` (the "this was not me" link: the marker's public id and a count, no personal data), `vendor.application_duplicate` (the address already had an account: outcome `resent` for a pending applicant or `notified` for any other account, nothing changed; the caller sees the same answer as a new applicant), `accounts.registration.rate_limited` (per-IP limit of the form), `accounts.action_link.sent` and `.used` for the confirmation link |
| Vendor establishment (CMP.13, D-21) | `vendor.establishment_outside_allowed` (a saved legal identity that moves an admitted vendor outside the allowed vendor countries); `core.platform_operator.allowed_vendor_countries_changed` (the allowed set, before and after). The nightly `vendors.check_establishments` job only logs |
| Erasure and retention (CMP.4) | `compliance.erasure_processed` (staff ran a pending erasure request now: whether it completed and the reason code from a fixed list, never free text; target `compliance.erasurerequest`, key the request's public id); `compliance.retention_run` (the nightly retention job: counts only, and whether it was a dry run) |
| Cross-vendor consent (CAT.14) | `vendors.cross_vendor_consent.set` (versioned vendor setting; note the `vendors.` prefix) |
| Orders (ORD.2) | `orders.create` (an order with its vendor sub-orders and line snapshots; addresses and e-mail are not recorded); `orders.transition` (an order or vendor sub-order changing state, with the derived order state); `orders.restrict` and `orders.anonymise` (D-29 scrub services; the entries carry flags only, never the personal data) |
| Payments (PAY.2) | `payments.payment.succeeded` (a customer payment the provider confirmed, from the signed webhook: provider, order reference, amount, currency and charge reference; no card data is ever held); the processed-event table is an idempotency record, not audited |
| Platform configuration (ARC.7) | `core.platform_operator.configured` |
| Feature flags (ARC.8) | `core.feature_flag.created`, `.updated` (staff switches, kill switches included); `vendor.feature_flag_saved`, `.feature_flag_deleted` (per-vendor overrides, under the vendor's key); see [feature-flags.md](feature-flags.md) |
| Email suppression (ARC.16) | `core.email.suppressed` (the address, the reason, hard bounce or complaint, and the provider message id; see [transactional-email.md](transactional-email.md)); the outbox itself is a delivery queue with its own append-only history (`core.outboxemailevent`), not audited |
| Image licences (CMS.8) | `core.image_licence.created`, `.updated` (the staff-editable allow list; NC and ND licences are refused, entries are never deleted). Images themselves are CMS content: uploads and edits are in Wagtail's action log |
| Field encryption keys (SEC.3) | `core.field_keys.rotated` (one per object, key ids only; see [field-encryption.md](field-encryption.md)) |
| Translation review (I18N.13) | `core.i18n.imported` (one entry per `i18n_import` run that applied texts: target `core.i18n_catalog`, key the language; `after` holds the file name, the file's SHA-256, the language and the counts of applied and skipped rows, never the texts; actor `command:i18n_import:<operating-system user>`, source `command`). A dry run or a refused import writes none; see [translations.md](translations.md) |
| Product prices (CAT.1) | `catalog.product.price_changed`, `catalog.variant.price_changed` |
| Products and variants (CAT.12) | `catalog.product.created`, `.updated`, `.deleted`, `.relations_changed` (categories, terms, upsells); `catalog.variant.created`, `.updated`, `.deleted`, through `catalog.services.save_product`, `delete_product`, `save_variant` and `delete_variant`. Entries show the sales mode, fiscal category, weight and dimensions (variants: weight override) with SKU and vendor; other fields are saved but not audited. Prices are refused there (price services only) and the owning vendor never changes (Q-33) |
| Redirects (CAT.6) | `catalog.redirect.created`, `.updated`, `.deleted` (the rule's kind, source, target and expiry), through `catalog.services.save_redirect` and `delete_redirect`; `import_redirects` writes one entry per row it applies |
| Events (EVT.3) | `events.event_created`, `.event_draft_saved`, `.event_hidden`, `.event_restored`, `.event_cancelled` (moderation and organiser actions, with the reason where staff give one), `.event_approved`, `.event_changes_requested` (the review workflow, from its signals); `events.policy_set`, `.policy_changed` (a vendor's moderation mode); `events.settings_set`, `.settings_changed` (the site's moderation threshold and retention) |
| Brands and manufacturers (CAT.20) | `catalog.brand.proposed` (a vendor's manual proposal, pending), `catalog.brand.approved`, `catalog.brand.rejected` (the entry lists the staff references of the products that lost the brand in `products_released`), `catalog.brand.merged` (two entries: the removed brand, and the target with its new product count; the 301 rules written by a merge are `catalog.redirect.created` and `.updated`), `catalog.brand.created`, `.updated`, `.deleted` (staff edits); `catalog.brandbatch.proposed` (one entry per import run, with the names of its new brands), `catalog.brandbatch.approved`, `catalog.brandbatch.rejected` (plus one brand entry per brand); `catalog.manufacturer.created`, `.updated`, `.deleted`. All through `catalog.brands`; entries of a vendor's proposal carry `vendor` (its public id) |
| Product slugs (CAT.5) | `catalog.product.slug_changed`, `catalog.product.old_slug_recorded` (the old slug kept for the 301) |
| Vendor SKUs (CAT.13) | `catalog.product.sku_renamed`, `catalog.variant.sku_renamed` (the old SKU kept as an alias), `catalog.sku_rows_updated` (stock updates by SKU) |
| Catalogue integrity (CAT.14) | `catalog.product.duplicated_to_vendor` (a product copied to another vendor, with the reason) |
| Shipping configuration (SHP.1) | `shipping.<model>.created`, `.updated`, `.deleted` for carriers, zones, vendor rates, weight bands and pickup options, through `shipping.services.save_config` and `delete_config`; deleting a rate also audits each of its bands |
| Compliance (CMP.3) | `compliance.gpsr_defaults.created`, `.updated` (a vendor's default GPSR data); `compliance.gpsr.created`, `.updated` (a product's GPSR data); `compliance.gpsr_text.created`, `.updated` (a per-language safety text); `compliance.country_languages.created`, `.updated` (the warnings languages of a country); `compliance.safety_case.opened`, `.closed` (staff actions on a safety case), `.overdue` (written once by the hourly `compliance.flag_overdue_safety_cases` job, actor `task:`). Through `compliance.services`, with `dry_run` |
| GDPR (CMP.4) | `compliance.gdpr_export` (a customer's data export: the user's public id and counts per section, never the data), `compliance.gdpr_erasure` (an account erasure: public ids and flags such as active, usable password, anonymised and staff; the orders it restricts or anonymises write their own `orders.restrict` and `orders.anonymise` entries) |
| Ingest (ING.1) | `ingest.source.created`, `.updated` (a supplier source); `ingest.run.created`, `ingest.run.status_changed` (an import run and its status machine); `ingest.review.opened`, `.decided` (a review item and a person's decision); `ingest.mapping.created`, `.updated`, `.deleted` (a vendor or platform mapping entry). Through `ingest.services`, with `dry_run` |
| Marketing suppression (L-3, PRM.9, PRM.16) | `compliance.suppression.added` (an address joined the suppression list: the first 12 characters of its HMAC digest and the reason, never the address). Through `compliance.services.suppress_email`, with `dry_run`; adding an address already listed writes nothing |
| Notice and action (CMP.5, DSA art. 16) | `compliance.notice.received` (a public report, actor `anonymous` with the client IP), `.review_started`, `.decided` (staff, with the outcome in the status), `.viewed` (staff opened a notice, which shows the notifier's name and email; the entry carries a reason, never the details). The anonymous child-abuse category stores no IP in the log. The location is kept as scheme, host and path only. The snapshot holds the target kind, location, category, status and flags only: the notifier's name, email and words stay in encrypted columns, never in the log. Through `compliance.services` (`submit_notice`, `start_review`, `decide_notice`), with `dry_run` |
| Statement of reasons and art. 18 (CMP.5 part 3, DSA art. 17, 18) | `compliance.statement.issued` (staff restricted a product, event or storefront through the owning app's service, which writes its own entry, and the vendor was sent its statement of reasons), `compliance.restriction.lifted` (staff lifted the hold on a restricted product), `compliance.notice.authorities_notified` (staff recorded a report to the authorities: authority and time). Snapshots hold ids, kinds and flags; the facts, the redress text and the note stay in the model. The statement is immutable once sent. A pending transparency-database record is stored with it; nothing is sent. Through `compliance.services` (`issue_statement`, `notify_authorities`), with `dry_run` |
| Online withdrawal (CMP.6 2/2) | `compliance.withdrawal.submitted` (one entry per vendor order the consumer withdrew from: statement reference, order number, vendor order public id, time, end of the period and language; never the buyer's email or name; actor `anonymous` with the client IP, or the signed-in user). The statement itself is written once and never changed. Through `compliance.withdrawal.submit_withdrawal`, with `dry_run` |
| Order emails (ORD.9) | Not audit actions: outbox templates queued by `orders.notifications` in the order transition's transaction, one idempotency key per event: `orders.order_received`, `orders.payment_confirmed`, `orders.order_confirmation` (per vendor, non-fiscal), `orders.vendor_new_order`, `orders.vendor_order_shipped`, `orders.vendor_order_delivered`, `orders.order_cancelled` (unpaid order), `orders.vendor_order_cancelled` (customer) and `orders.vendor_order_cancelled_notice` (vendor: own lines only). The context never holds payment references, commission or the buyer's tax identifiers; a vendor mail has its own lines and the ship-to address only. A refused recipient is logged (class only) and skipped, and never rolls the transition back |
| Shipping templates (SHP.13) | `shipping.shippingtemplate.created`, `.updated` (a shipping template saved through `shipping.config_io`, besides the `shipping.<model>.*` entries above) |
| Shipping configuration import (SHP.13, SHP.14) | `shipping.config.imported` (one entry per import run, besides the per-row `shipping.<model>.*` entries) |
| Tax configuration (TAX.1) | `tax.rate.configured`, `tax.vendor_tax_profile.configured` (versioned: the replaced row is `before`), `tax.territory.created`, `.updated`, `tax.legal_note.created`, `.updated`; VIES answers (`tax.vatvalidation`) are an append-only cache, not audited |
| Site themes (THM.1) | `themes.theme_settings.configured` (the site's hostname and theme, before and after; see [themes.md](themes.md)) |
| Search engine visibility (SEO.8) | `seo.settings.configured` (platform staff turn a site's indexing switch on or off in the SEO panel; the site id and `allow_indexing`, before and after; allowing is refused while `MPMV_FORCE_NOINDEX` or demo mode is on) |
| Legal pages (CMS.5, I18N.9) | `cms.legal_page_version.recorded` (a published legal page version), `cms.legal_page_review.recorded` (a per-language review), `cms.legal_page.effective_date_set` |
| Tax regime changes (TAX.2) | `tax.regime_change.requested`, `.approved`, `.rejected` (who, the requested regime, the review reason); an approval also writes `tax.vendor_tax_profile.configured` and, when it moves the declared fiscal regime, `vendor.updated` |
| Garage scheme and nodes (FIT.1, D-38) | `fitment.scheme.updated` (wording, year range, limits: before and after); `fitment.level.created`, `.updated` (labels, naming, order), `.deleted`; `fitment.node.created` (a node or a proposal), `.updated` (rename, translated name), `.status_changed` (approve, retire, reactivate: one entry per node), `.merged` (the merged node, its target, the children and references moved), `.refused` (a proposal removed, with the reason), `.deleted`; `fitment.alias.created`, `.deleted`; `fitment.category.relevance_changed` (one entry per category the operator marks as expected to declare fitment). Through `fitment.services`, with `dry_run` |

Not audited here on purpose: blog articles created by `cms.services.import_article` (CMS.3) and
other CMS page content. Wagtail's page history and revisions record them, like an editor's
publish; the service is listed in `tests/unit/test_audit_coverage.py` with that reason.

Deliberately not stored: password hashes (only `has_usable_password`), API key hashes, TOTP
secrets, and the identifier typed in a failed login that matches no account (it may be a typo
of a real address or a password typed in the wrong field; the entry's key is `unknown`).
Failures refused during a lockout are not audited one by one: the lockout entry covers them.

## Rules for new code

1. **Classify every model.** Each app's `AppConfig` lists `audited_models` and
   `unaudited_models` (`{name: reason}`); models of other packages that the app manages in the
   admin use their full label (`"auth.group"`). `manage.py check` (and so `make check` and CI)
   fails with `core.E201` for an unclassified model and `core.E202` for an exemption without a
   reason.
2. **Write audited models only through services.** A mutating service takes `ctx:
   AuditContext` and `dry_run`, builds a `ChangeSet` and applies it with `run_mutation` (or
   `save_audited` for a plain create-or-update). `tests/unit/test_audit_coverage.py` fails for
   a service that offers `dry_run` without `ctx`, unless it is listed there with the reason.
   Views and admins build `ctx` with `apps.core.audit.audit_context(request)`, which records
   the trusted-proxy client IP (`apps.core.http.client_ip`); do not write another helper. The
   API builds its own context, because its actor is the API client.
3. **Admins call those services.** The admin of an audited model overrides `save_model`,
   `delete_model` and `delete_queryset` (plus `save_related` for many-to-many fields and
   `save_formset` for audited inlines), or denies the permission. Otherwise the system check
   fails with `core.E203`. An override of `save_model`, `delete_model` or `delete_queryset`
   that calls Django's default through `super()` counts as not overridden: it saves the row
   directly, so it fails too. Admin actions must call services too (see `vendors.admin`).
   Site-level admin views are not ModelAdmins, so the check cannot see them: the staff site
   (`apps.accounts.admin_site.AuditedAdminSite`, installed as `admin.site`) audits the
   self-service password change, and any new site-level view that writes must do the same.
4. **Snapshots** are plain values: public ids instead of foreign keys, no secrets or hashes,
   `Decimal` kept exact (stored as strings). Objects owned by a vendor include
   `"vendor": <vendor public id>`, which fills the entry's `vendor_key` for the vendor filter.
5. **Key entries by a stable id** (`public_id`), never by an editable slug or name, unless the
   model has nothing else (Django groups use their unique name).
6. **Sensitive views** (IBAN, payouts, rates, data exports) use `@step_up_required` (CORE.4).

## Hooking in rate models (TAX, SHP, commissions)

`apps.tax.services.configure_tax_rate` (TAX.1) is the reference implementation of this pattern.
Rates, zones and commission rules are versioned configuration, like `PlatformOperator`: a
change is a new row valid from a date, closing the current one, so orders and documents keep
pointing at the version they were computed with. The audit entry of the new row carries the
row it replaces as `before`, so the staff view shows the rate change with before/after:

```python
TAX_RATE_MODEL = "tax.taxrate"
RATE_CHANGED = "tax.rate.changed"
RATE_FIELDS = ("country", "category", "rate", "valid_from", "valid_to")


def rate_snapshot(rate: TaxRate) -> dict[str, Any]:
    return {name: getattr(rate, name) for name in RATE_FIELDS}


def change_rate(new: TaxRate, *, ctx: AuditContext, dry_run: bool = False) -> ChangeSet:
    current = current_rate(new.country, new.category, at=new.valid_from)  # row locked
    changeset = ChangeSet()
    changeset.update(
        TAX_RATE_MODEL,
        str(current.public_id),
        {"valid_to": current.valid_to},
        {"valid_to": new.valid_from},
    )
    changeset.create(
        TAX_RATE_MODEL, str(new.public_id), rate_snapshot(new), supersedes=rate_snapshot(current)
    )

    def apply() -> None:
        current.valid_to = new.valid_from
        current.save(update_fields=["valid_to", "updated_at"])
        new.save()

    return run_mutation(changeset, apply, ctx=ctx, action=RATE_CHANGED, dry_run=dry_run)
```

Then add `"taxrate"` to the app's `audited_models`, route its admin through `change_rate`, and
add a regression test that a rate change is listed under `?target_model=tax.taxrate` with the
old and new rate in its *Changes* table (see
`apps/core/tests/integration/test_audit_admin.py` for the platform operator version). A
vendor-specific shipping zone or commission rule also puts `"vendor"` in its snapshot.

## Personal data in the audit trail (CMP.4)

- `actor_label` is `user:<public id>` or `staff:<public id>` (`apps.core.audit.user_context`),
  never the email. The table is append-only (database trigger), so an address written there could
  not be removed with the account. Owner decision (lane-a answer 6784046561): customer rows
  written before this change were rewritten to `user:<public id>` (`unknown` when the user no
  longer exists) by the one-off migration `core/0021_audit_actor_pseudonymised`. It is the
  single exception to the append-only rule: it disables the trigger for that one UPDATE inside
  its transaction, enables it again, and writes one audit row with counts only. Staff rows keep
  `staff:<email>`: the identity of staff is kept to establish and defend legal claims (GDPR
  article 17(3)(e)) and for the order retention duties of D-29 and D-30 (article 17(3)(b)); it
  goes no longer than those periods.
- `ip` is kept as security-log evidence (GDPR article 6(1)(f), the platform's legitimate interest
  in detecting abuse, and article 17(3)(e) for legal claims). It is not erased with the account.
  A retention period for it (null the column after N months) is a CMP.4 2/2 / CMP.29 decision.
- `before` and `after` of the GDPR actions hold public ids, flags and counts only.
- `OutboxEmailEvent` rows are append-only too. The erasure writes the detail `account erased`;
  provider feedback text in older rows may quote an address and is a documented residual.
- A lockout entry's target key (`login:account:<keyed hash>`) never holds the address; the same
  migration rewrote old ones. A former staff member keeps the `staff:<email>` label of rows
  written while they were staff (article 17(3)(e) basis above).
