# Attack scenarios (SEC.5)

Tests that play the attacker against our own code (owner security wave, item 10). One module per
category. Each case asserts that the attack fails and leaves an audit or log trace where one is
expected. A case that an existing test already covers is not repeated: it is listed below with
the covering test.

The modules run with the integration suite (`make test`, so on every PR) and carry the `security`
marker: `uv run pytest -m security` runs only them. A real vulnerability that is not fixed in the
same change is an `xfail(strict=True)` test with the finding as its reason, so the test turns red
once the fix lands and the marker must go.

External testing (OWASP ZAP baseline, nuclei, path fuzzing, slowloris, load and abuse tests) runs
from STAGE (lane A, ARC.21); the external penetration test (SEC.6) is done by people before
go-live.

## Categories

| Category | Module | Status |
|---|---|---|
| Accounts | `test_accounts_attacks.py`, `test_passkey_window_attacks.py`, `test_session_lifetime_attacks.py` | present |
| Request tampering | `test_request_tampering.py` | present |
| Business logic | `test_business_logic.py` | to do |
| Integrations | `test_integrations.py` | to do |
| Resource abuse | `test_resource_abuse.py` | to do |
| Information leaks | `test_information_leaks.py` | to do |
| AI and agents (SEC.9) | `test_ai_agents.py` | to do |

## Cases covered elsewhere

### Accounts

| Case | Covering test |
|---|---|
| Login enumeration by message (wrong, unknown, locked) | `apps/accounts/tests/integration/test_login_protection.py::test_locked_wrong_and_unknown_accounts_get_the_same_generic_error` |
| Unknown emails locked like real ones | `apps/accounts/tests/integration/test_login_protection.py::test_unknown_emails_are_locked_exactly_like_real_ones` |
| Brute force of one account | `apps/accounts/tests/integration/test_login_protection.py::test_after_n_failures_even_the_right_password_is_refused` |
| Spraying from one IPv6 /64 | `apps/accounts/tests/integration/test_login_protection.py::test_an_ipv6_client_is_locked_by_its_64_network` |
| TOTP replay (same and another session) | `apps/accounts/tests/integration/test_mfa_totp.py::test_a_code_cannot_be_replayed` |
| Recovery code reuse | `apps/accounts/tests/integration/test_mfa_recovery_codes.py::test_a_recovery_code_verifies_once_and_is_audited` |
| Step-up older than the window | `apps/accounts/tests/integration/test_mfa_step_up.py::test_an_old_verification_asks_for_the_second_factor_again` |
| Action without step-up after a magic sign-in | `apps/accounts/tests/integration/test_action_links.py::test_an_action_without_step_up_is_refused` |
| Session still verified after its MFA device is deleted | `apps/accounts/tests/integration/test_mfa_enforcement.py::test_deleting_the_verified_device_closes_the_session_again` |
| Escalation through the Django user admin (staff, superuser, groups, permissions) | `apps/accounts/tests/integration/test_admin_escalation.py` |
| Escalation through the Wagtail user and group pages | `apps/cms/tests/integration/test_cms_admin.py::test_users_and_groups_are_not_managed_in_the_wagtail_admin` |

| Do it once or a window for shop A reused on shop B by an owner of A who is staff of B (also demotion, expiry, forged forms) | `tests/security/test_passkey_window_attacks.py` (the owner-of-both case: `apps/manager/tests/integration/test_shop_owner_passkey.py`) |
| Login CSRF and unsafe-method CSRF on every web route | `tests/integration/test_endpoint_security.py::test_post_without_csrf_token_is_refused` |
| IDOR by `public_id` across vendors and customers (sweep) | `tests/integration/test_endpoint_security.py::test_a_customer_cannot_open_vendor_b_objects`, `tests/integration/test_tenant_isolation.py` |

### Request tampering

| Case | Covering test |
|---|---|
| Extra `vendor` naming another vendor in an API payload | `apps/api/tests/integration/test_v1_catalog.py::test_a_vendor_key_cannot_create_for_another_vendor` |
| `is_staff` and other access fields POSTed by staff to the user admin | `apps/accounts/tests/integration/test_admin_escalation.py::test_staff_cannot_grant_staff_status_groups_or_permissions` |
| Action-link emails use `SITE_BASE_URL` | `apps/accounts/tests/integration/test_action_links.py::test_the_email_helper_queues_the_link_through_the_outbox` |
| Wagtail password reset page closed to a signed-in superuser | `apps/cms/tests/integration/test_cms_admin.py::test_passwords_are_not_changed_from_the_wagtail_admin` |
| `frame-ancestors 'none'` and `X-Frame-Options: DENY` with the LIVE settings | `tests/integration/test_security_headers.py::test_live_security_headers_are_present`, `test_live_csp_is_strict` |

## Not testable yet

- Enumeration through registration, password reset and vendor claim: no such page exists yet
  (customer accounts, CORE.19, VEN.24). A skipped test keeps the gap visible.
- Privilege escalation through impersonation: CORE.10 is not implemented. A skipped test keeps
  the gap visible.
- Mass assignment of `price` or `status` in customer forms: no customer form (cart, checkout,
  account) exists yet; the API and admin sweeps run today.
