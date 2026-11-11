# ADR-0009: PDF library for the per-vendor order confirmation

- **Status:** Proposed (owner or lane-a to accept)
- **Date:** 2026-11-11
- **Deciders:** project owner, tech lead
- **Backlog:** ORD.9 (part 3/3) · **Open decision:** none

## Context

[Spec 07](../spec/07-cart-orders-payments.md) ORD.9 requires the order confirmation to be sent as
an email and as a PDF, one per vendor, labelled "Documento non fiscale / Non-fiscal document" in
EN, IT, DE, FR and ES, carrying the order reference and never an invoice number
([spec 11](../spec/11-compliance.md): invoices and receipts are the vendor's). Parts 1 and 2 of
ORD.9 (emails) are merged or in review; part 2 left the PDF open because the repository has no PDF
library. The standing item on the Lane board asks for "WeasyPrint vs ReportLab: image size, system
libs, CVE surface".

Facts that constrain the choice:

- The app image is `python:3.13-slim-trixie` with no apt packages and no build tools in the runtime
  stage ([Containerfile](../../Containerfile)). Python dependencies are pinned in `uv.lock`
  ([ADR-0001](0001-stack.md)); `uv.lock` already holds `pillow` and `cffi`, and no PDF package.
- Content is partly untrusted: vendor names, product names, addresses and order notes come from
  users and vendors.
- The document is 1 to 3 pages, rendered rarely (once per vendor order), never in the request path
  of a hot endpoint.
- One person maintains the platform; every system library is a patch obligation (OPS.4).
- We already have HTML email templates per language under `apps/orders/templates/email/orders/`
  and theme CSS ([ADR-0008](0008-frontend-toolchain-and-themes.md)).

## Options considered

Versions and advisories checked on 2026-11-11 (PyPI, GitHub Advisory Database); re-check before
accepting.

| | WeasyPrint | ReportLab (open source) | No PDF (HTML email plus print CSS) |
|---|---|---|---|
| Licence | BSD-3 | BSD-style | none |
| Current release | 70.0 (frequent releases, several a year; Kozea / CourtBouillon, active) | 5.1.0 (ReportLab Inc., steady, slower) | n/a |
| Advisories, last 3 years | GHSA-35jj-wx47-4w8r (2024-03, files/URLs attached to PDF), GHSA-983w-rhvv-gwmv (2026-01, SSRF bypass via redirects), GHSA-jhhc-3hcp-qhm5 (2026-06, CSS injection via presentational hints), GHSA-jf6q-chmf-3h3v (2026-09, SSRF), GHSA-r543-q48m-4c9j (2026-09, EPS images reach Ghostscript, RCE) | CVE-2023-33733 (RCE in `rl_safe_eval`, fixed in 3.6.13, 2023-04; just outside the window). None found for 2024 to 2026 | none |
| System packages | Pango (>= 1.44), HarfBuzz (+ subset), Fontconfig, a font package; about 5 apt packages plus fonts | none for the default build (pure Python plus optional C accelerators; Pillow already locked) | none |
| Image size delta (estimate, to measure in the PR) | roughly +30 to +50 MB (Pango, HarfBuzz, Cairo-free stack, fontconfig, one font family) | below 5 MB; bundled fonts only cover Latin, extra TTFs are added by us | 0 |
| HTML/CSS reuse | Renders our existing HTML and CSS (flexbox, grid, `@page`, `@font-face`); one template language | None: a second layout in Python (Platypus flowables); the strings must be re-expressed in code | Reuses everything |
| Accessibility | `pdf_variant="pdf/ua-1"` and `pdf_tags` exist, documented as "not guaranteed to be valid", so we verify with veraPDF | Tagged PDF is not supported in the open-source edition | HTML is the accessible form (semantic markup, axe gates of THM.5) |
| Fonts and i18n | Full shaping through Pango and HarfBuzz; any installed or `@font-face` font; IT/DE/FR/ES accents work | Core Type 1 fonts cover Latin-1 (accents ok); Unicode needs registered TTF files | Browser fonts |
| Untrusted content | HTML parsed by a real engine; risks are `url()`, `<link>`, `<img src>`, attachments and CSS injection. Mitigated by a restrictive fetcher (below) | Own mini-markup parser (`Paragraph`) with a history of `eval` bugs (the CVEs above) and an SSRF CVE in `<img>`; text must be escaped | Django autoescape only |
| Performance (1 to 3 pages) | About 0.3 to 1 s per document, CPU-bound, so it runs in the task queue, not in a request | About 50 ms | none |
| Testability | Text extraction in tests with `pypdf` or `pdfminer.six` (test-only dependency); also `pdf/ua` validation by veraPDF in CI optional | Same | HTML assertions, no new tooling |
| Meets the ORD.9 "PDF" wording | Yes | Yes | No: needs a spec change and owner acceptance |

Notes on the options:

1. **WeasyPrint.** One template language, good i18n, tagged PDF. Costs: system libraries in the
   runtime image, a larger and more active CVE surface, and a fetcher that must be locked down.
   Its recent advisories are almost all about fetching resources, which a no-network fetcher
   removes as a class.
2. **ReportLab.** Smallest image and no system libraries. Costs: every language variant is laid out
   in Python, no tagged PDF in the open-source edition (WCAG-relevant for a document sent to
   consumers), manual font registration, and a markup parser with a poor security record.
3. **No PDF.** Zero cost and zero surface, but the spec explicitly asks for a PDF, and a
   downloadable print page is not a document the customer can archive or forward unchanged.
   Kept as the fallback if the owner prefers not to add a dependency.

## Decision

**Option 1, WeasyPrint, with the hardening below.** Status stays Proposed until the owner or lane-a
accepts; no dependency is added by this ADR.

Hardening (each point is a test in the implementation task):

- **A custom `url_fetcher` that allows only `data:` URIs and files under an allow-list of static
  roots** (`STATIC_ROOT` and the `static/` and `themes/*/static/` source directories, after
  `realpath` resolution and with `..` and symlink escapes rejected). Any other scheme (`http`,
  `https`, `ftp`, `file` outside the roots) raises, so there is no network access and no SSRF via
  `url()`, `<link>`, `<img>` or `@import`. Redirects are never followed because nothing is
  fetched over HTTP. The fetcher is the only way the renderer is called; a unit test fails if code
  calls `HTML(...)` without it.
- **`base_url` is set to a fixed internal value**, never to a user-controlled URL, and the template
  contains no `<link rel="attachment">`, no `<object>`, no `<embed>` and no EPS or SVG from
  users (GHSA-35jj-wx47-4w8r, GHSA-r543-q48m-4c9j). Images in the document are only the platform
  logo (static) and, if wanted, nothing from vendor uploads.
- **Autoescaped Django template, no `|safe`, no presentational attributes from user data**
  (GHSA-jhhc-3hcp-qhm5). A test renders hostile vendor and product names (`<img src=x>`,
  `url(http://...)`, `</style>`) and asserts no fetch is attempted and the text appears escaped.
- **Rendering runs in the task queue worker** ([ADR-0004](0004-task-queue.md)), with a timeout and
  a size cap on the output, never inside a web request. The worker has no more privileges than
  the web process (non-root, read-only code), and outbound traffic from the worker should be
  limited to what the app needs (to be checked against the deploy setup).
- **Packages:** the Containerfile runtime stage installs only the minimum apt packages
  (`libpango-1.0-0`, `libpangoft2-1.0-0`, `libharfbuzz-subset0`, `fontconfig`, one font package
  such as `fonts-dejavu-core` or a bundled OFL font under `static/`), removes apt lists, and pins
  nothing newer than Debian's security updates. The image size delta is measured and recorded in
  the PR. Wheels come from `uv.lock` (hash-pinned); `weasyprint` is added with the usual pip-audit
  and Dependabot coverage (OPS.4), and the version is tracked on the security watch list.
- **Accessibility:** `pdf_variant="pdf/ua-1"` and `pdf_tags=True`, plus document language and
  title; an optional CI job validates one sample with veraPDF. Because WeasyPrint says the output
  is not guaranteed valid, the email body stays the primary, fully accessible form and the PDF is
  an attachment.

## Consequences

- One template language for email and PDF per language; the five-language snapshot tests of ORD.9
  can share strings.
- The runtime image grows and gains five system packages that need security updates; the weekly
  image rebuild and the image scan (OPS.4) must cover them; check that when accepting.
- WeasyPrint's advisory rate is high: expect to bump it several times a year. The fetcher
  hardening limits the impact of the fetch-related classes.
- Revisit if the image delta or the patch burden proves too heavy (switch to ReportLab with
  registered TTF fonts, accepting no tagged PDF), if the owner drops the PDF requirement (option 3),
  or if a WeasyPrint advisory shows a way around the fetcher.

## Implementation plan: ORD.9 part 3/3 (after acceptance)

1. **Dependency and image (one PR, no feature).** Add `weasyprint` to `pyproject.toml` and
   `uv.lock`; `pypdf` as a dev-only dependency for text extraction. Add the apt packages to the
   runtime stage of the `Containerfile` and the CI image; record the measured size delta; check
   `make check` and the image build on CI.
2. **Failing tests first** (`apps/orders/tests/unit` and `integration`): text extraction for each
   of the five languages shows the "Documento non fiscale / Non-fiscal document" label (localised
   per language), the order reference, the vendor's own lines only, and never the words invoice or
   receipt nor an invoice number; the fetcher rejects `http`, `file` outside the roots and
   redirects; hostile names render escaped without a fetch; the PDF is tagged (document language
   and title set); a restricted (non-`active`) order produces nothing (D-30).
3. **Engine and service.** `apps/orders/documents.py` (or `services.py` function) with
   `render_vendor_confirmation_pdf(vendor_order) -> bytes`: builds the context from order
   snapshots only (prices, tax, commission data are snapshots, never live references), renders
   `templates/documents/orders/vendor_confirmation.html` plus a small print stylesheet
   (`@page` A4, margins, footer with page numbers) through the locked-down fetcher.
   The template reuses the email string catalogs (I18N.5) and the order language with EN fallback.
4. **Delivery.** The notification of ORD.9 part 1 and 2 attaches the PDF for the order language;
   rendering is a task in the queue, enqueued in the caller's transaction with one idempotency
   key per vendor order and event, and the email is sent by the outbox only when the attachment
   exists (a failed render retries, then sends the email without the PDF and logs an audit entry).
5. **Storage and retention: generate on the fly, do not store.** The document is a pure function
   of the order snapshots, so we render at send time and again on demand from the customer's order
   page (signed, authenticated download, `Cache-Control: private, no-store`). No file is kept, so
   there is no new retention class, no extra GDPR export or anonymisation target (CMP.4) and no
   storage to back up. If the owner later wants the exact bytes that were mailed, store the PDF
   in private media with the order's retention period and add it to the anonymisation job; that is
   a separate decision.
6. **Docs and gates.** Update `docs/dev/audit-trail.md` for the new audit action, the security
   registry for the download endpoint, and the spec row for ORD.9 only if the owner chooses
   option 3. Flag the PR for human review (order code, personal data).
