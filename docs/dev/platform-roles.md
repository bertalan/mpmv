# Platform roles and the role-permission matrix (CORE.18)

<!-- Generated from apps/manager/roles.py by `make docs`; do not edit by hand. -->

## Roles

| Role | Group | Permissions |
|---|---|---|
| Configuration and tax | Platform: configuration and tax | `manager.platform_config` |
| Catalog and moderation | Platform: catalog and moderation | `manager.platform_catalog`, `vendors.view_all_vendors`, `compliance.review_notices` |
| Finance and payments | Platform: finance and payments | `manager.platform_finance`, `vendors.view_all_vendors` |
| Support | Platform: support | `manager.platform_support`, `vendors.view_all_vendors` |
| Platform owner | Platform: owner | `core.platform_owner`, `vendors.view_all_vendors`, `compliance.review_notices` |

Incompatible pairs (refused unless the one-person-team setting is on): Configuration and tax + Finance and payments.

## Matrix

E edit, R read only, A audited support action, I impersonation within the policy, empty means no access (404).

| Section | Configuration and tax | Catalog and moderation | Finance and payments | Support | Platform owner | Tech. superuser |
|---|---|---|---|---|---|---|
| Platform: dashboard | R | R | R | R | R |  |
| Platform: configuration and tax (ARC.24) | E |  |  |  | R |  |
| Platform: owner-only settings (CMP.13, CMP.11 approvals) | R |  |  |  | E |  |
| Platform: catalog, categories, attributes, taxonomies, imports |  | E |  | R | R |  |
| Platform: moderation (vendor events, storefront changes, content) |  | E |  |  | R |  |
| Platform: notices of illegal content (CMP.5) |  | E |  |  | R |  |
| Platform: vendors (applications, plans) | R | R | R | R | E |  |
| Platform: approve or suspend a vendor (step-up, reason) |  | E |  |  | E |  |
| Platform: close a vendor definitively |  |  |  |  | E |  |
| Platform: orders and customers |  |  | R | R+A | R |  |
| Platform: support actions (unlock, resend link) |  |  |  | A | A |  |
| Platform: account activation and service messages (CORE.19) |  |  |  | A | E |  |
| Platform: start an impersonation session (CORE.10) |  |  |  | A |  |  |
| Platform: impersonation policy and full-access grants | R |  |  | R | R | E |
| Platform: one-person-team setting (BO-13) |  |  |  |  | R | E |
| Platform: finance (payouts, balances, refunds, reconciliation) |  |  | E |  | R |  |
| Platform: tax and DAC7 reports |  |  | E |  | R |  |
| Platform: staff and roles |  |  |  |  | E |  |
