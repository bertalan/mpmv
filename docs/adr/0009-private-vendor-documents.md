# ADR-0009: Private vendor documents (storage, versions, signed reads, audit)

- **Status:** Proposed
- **Date:** 2026-11-11
- **Deciders:** project owner, tech lead (storage backend choice: lane-a and owner, see Open points)
- **Backlog:** ARC.15 (storage), VEN.32 (versioned documents), VEN.31 (document link), SEC.2,
  CMP.29, CORE.18

## Context

Vendors hold documents that must never sit on a public URL: terms, return policy, data sheets,
contracts and transport proofs. The owner requirement (issue #86, extending decision B on VEN.31)
is: each vendor has its own private area; documents are versioned by SHA-256, a new upload is a
new immutable version; every read goes through a signed, expiring link and is logged; the order
snapshot stores the hash of the document a customer relied on; retention follows the order's
legal retention (CMP.29).

Today ARC.15 names one backend (S3-compatible object storage with django-storages). STAGE is a
single VPS (ARC.13) with a read-only container filesystem, and no object storage exists yet.
Public media (product images, Wagtail images) stays on its own path and CDN and is out of scope.

## Options considered (storage)

1. **Local private backend, served by the app.** Files live on a dedicated volume outside
   `MEDIA_ROOT` and outside any Caddy `file_server` root. A Django view authorises the read,
   writes the audit entry, then answers with an internal redirect header (`X-Accel-Redirect`
   style; Caddy offers `handle_response` with `file_server` on an internal-only route, or
   `X-Sendfile` where available), so Caddy streams the bytes and the app does not.
   Pros: nothing new to buy or secure, works on STAGE now, one audit point, no URL ever leaves
   the platform. Cons: the volume must be backed up and restored by us (OPS), no CDN offload,
   single host.
2. **S3-compatible object storage, private bucket, presigned URLs.** The app authorises and
   audits, then redirects to a short presigned GET (TTL of 60 seconds or less).
   Pros: durable and replicated, offloads bytes, fits the later multi-host shape.
   Cons: the presigned URL is a bearer credential that leaves our audit point (the read is
   logged when the link is issued, not when the bytes are fetched), provider dependency and cost,
   the bucket policy becomes a security-critical configuration.
3. **Store in PostgreSQL (large objects or bytea).** Single backup and transactional with
   metadata, but bloats the database and its backups and does not scale. Rejected.

## Decision

- A **storage interface** `PrivateDocumentStore` (put, open, delete, exists, all addressed by
  `(vendor, sha256)`) hides the backend. Settings choose it per environment.
- **Phase 1 (STAGE, and LIVE until volume justifies more): option 1**, the local private backend
  behind the app with an internal redirect through Caddy. **Phase 2: option 2** behind the same
  interface, with the app still issuing the only link a client ever sees (our signed URL; the
  presigned one is generated server side and followed by a redirect of at most 60 seconds, and a
  fetch-time audit is not available, so the issue-time entry is the record). The backend change
  needs no data-model change. The infra choice and the switch date go back to lane-a and the
  owner (Open points).
- **Layout:** `private/<vendor id>/<first two hex>/<sha256>`; no original filename in the path.
  The original filename is metadata in the database and is sanitised on output.
- **Immutable versions.** A `VendorDocumentVersion` row holds vendor, document, sha256, size,
  sniffed media type, uploader, created at. The blob is write-once: `put` refuses an existing
  path with different bytes; the same bytes uploaded twice resolve to the existing version.
  Database constraints and a trigger forbid UPDATE of the hash and DELETE of a version
  referenced by an order. The "current" version is a pointer on the document.
- **Upload pipeline:** SEC.2 (type sniffing, size cap, re-encoding of images, EXIF stripped).
  PDFs reuse the SEC.2 active-content check (no JavaScript, no launch or embedded-file actions,
  no XFA, encrypted PDFs refused); an antivirus scan runs when SEC.2 provides one. A file that
  fails is never stored.
- **Access rules** (one function, `can_read_document(user, version)`, default deny):
  - vendor owner and members of the owning vendor (`current_vendor`, CORE.3);
  - platform staff only by CORE.18 section (Catalog and moderation, Finance and payments, Support,
    owner, technical superuser as the matrix says), never by `has_perm`;
  - a customer only when the version's hash equals a hash stored in the snapshot of one of
    their own orders (customer identity from the session or the order access token), and only
    for documents the vendor marked customer-visible;
  - everyone else: 404 (not 403), so existence is not revealed.
- **Signed links.** URL `/documents/<version public id>/?t=<token>`. The version public id is
  a random UUID, not the hash. The token is an HMAC-SHA256 over
  `purpose | version id | user id or session id hash | expiry | nonce` with a key derived
  (HKDF) from a dedicated secret `DOCUMENT_LINK_KEY`, never `SECRET_KEY` and never shared with
  other signers. TTL 5 minutes by default (max 15). Bound to the authenticated user or, for
  an anonymous customer with an order token, to the session. Single-use is optional per
  document class (on for contracts and customer reads: the nonce is recorded in a short-lived
  table and consumed on first read). Verification uses constant-time comparison and checks
  authorisation again at read time, so revoking a member's access also kills open links. A
  link is issued by an authenticated page, never embedded in emails or public pages; the VEN.31
  storefront rich-text link points at a view that issues a fresh link on click.
- **Per-read audit.** Each issued link and each served read writes an `AuditLog` entry
  (action `document.read`): document, version id, sha256, reader kind, reader id, time, result
  (served, expired, tampered, denied). Staff and vendor members are recorded by user id.
  Customers are recorded by an HMAC pseudonym of their id (key `AUDIT_PSEUDONYM_KEY`), never the
  plain name, email or address. Denied attempts on a foreign document are audited with the
  requesting user, and rate-limited.
- **Order snapshot.** ORD.2 stores, per order, the hash and version id of the terms and return
  policy in force at checkout. A referenced version cannot be changed or deleted.
- **Retention.** A version is kept while any order references it: until the longest legal
  retention of those orders ends (CMP.29 `tax_retention_until`), then it becomes eligible. An
  unreferenced version may be removed by the vendor (blob removed when no other version row of
  that vendor points at the same hash). Audit entries of reads follow the AuditLog retention and
  keep only pseudonyms. A transport-proof file with personal data keeps the CMP.29 deletion rule.
- **Download headers.** `Content-Disposition: attachment; filename*=` (sanitised name),
  `X-Content-Type-Options: nosniff`, the stored sniffed `Content-Type` (never the client's),
  `Content-Security-Policy: sandbox; default-src 'none'`, `Cache-Control: private, no-store`,
  `Referrer-Policy: no-referrer`, `Cross-Origin-Resource-Policy: same-origin`. Documents are
  never rendered inline on the platform origin.
- **Backups and restore.** The private volume (phase 1) or bucket (phase 2) is in the OPS backup
  set, encrypted, with the same schedule as the database; the metadata (hashes) is in the
  database dump. A restore drill verifies every blob against its sha256 and reports missing or
  corrupted ones. The key `DOCUMENT_LINK_KEY` is stored with the other secrets and is not in the
  backup of the volume. Rotating it invalidates open links only.

## Threat model

| Threat | Mitigation |
|---|---|
| IDOR: guess another vendor's document id | Random UUID ids, per-read `can_read_document`, 404 on denial, denied attempts audited and rate-limited, tenant-isolation tests (CORE.3) |
| Link leakage through Referer, browser history, proxy or access logs | Short TTL, user or session binding, optional single use, `Referrer-Policy: no-referrer`, token excluded from Caddy and app access logs (log the path only), links never in emails or public pages |
| Enumeration of versions or hashes | Public id is not the hash; the hash is never in a URL; uniform 404 |
| Replay of a captured link | Expiry plus binding plus single-use nonce for sensitive classes; re-authorisation at read time |
| Forged or altered token | HMAC with a purpose-specific key, constant-time check, expiry inside the signed payload |
| Active content in a download (PDF script, SVG, HTML) | SEC.2 checks at upload; `attachment`, `nosniff`, sandbox CSP, no inline rendering, allow-list of media types (no HTML, no SVG) |
| Overwrite or tampering with a relied-on version | Write-once blobs, hash-addressed, DB trigger on referenced versions, restore drill verifies hashes |
| Path traversal or direct file access | Path built only from `(vendor id, sha256)`, volume outside `MEDIA_ROOT` and outside any static root, internal-only Caddy route (not reachable by a client) |
| Bearer URL in phase 2 skipping our audit | TTL of 60 seconds or less, issue-time audit, bucket private with no public policy, access keys scoped to one bucket |
| Customer privacy in the audit trail | Pseudonymised reader, no plain identifiers in `AuditLog` |
| Staff over-reach | CORE.18 section check, each staff read audited with the user |

## Consequences

- STAGE gets private documents without new infrastructure; object storage stays a later,
  reversible choice behind one interface.
- Every read costs a database write (audit) and an authorisation check; acceptable for document
  volumes. Streaming goes through Caddy, so the app is not tied up by downloads.
- Deduplication is per vendor (same bytes in two vendors are two blobs), to keep areas separate
  and deletion independent.
- A referenced version can never be removed early, which can keep a vendor's file longer than the
  vendor wants; this is the intended legal-proof behaviour.
- ARC.15 changes from "Hetzner Object Storage" to "private storage interface, local backend
  first". Public media storage keeps its own path.

## Open points (back to lane-a and the owner)

1. Backend for LIVE: stay local, or move to S3-compatible storage and when (cost, durability,
   second host). Default proposal: local on STAGE, decide before the first LIVE vendor upload.
2. Which document classes are single-use (proposal: contracts and anything a customer reads).
3. Default link TTL (proposal 5 minutes) and maximum (15).
4. Whether the antivirus scan (for example ClamAV in a container) is required in phase 1 or the
   SEC.2 structural checks are enough.
5. Whether the vendor may mark a document customer-visible, or only the platform.
