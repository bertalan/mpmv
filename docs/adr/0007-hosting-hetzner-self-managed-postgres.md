# ADR-0007: Hosting on Hetzner with self-managed PostgreSQL

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** ARC.5, ARC.13, ARC.15, OPS.1 · **Closes:** D-12 · **Supersedes:** [ADR-0005](0005-hosting.md)

## Context

[ADR-0005](0005-hosting.md) chose an EU VPS for the app plus managed PostgreSQL and left the
provider to ARC.5. ARC.5 delivered the image, the local stack, the Quadlet units and the deploy
and rollback tooling, but STAGE could not exist without a provider. The owner has now chosen
the provider and, with it, to run PostgreSQL ourselves.

The constraints from ADR-0005 still apply: EU data residency (GDPR), point-in-time recovery, and
the same OCI images we build locally ([ADR-0002](0002-local-containers-apple.md)). Spec 12 §12.4
sets the backup targets (RPO ≤ 15 min, RTO ≤ 4 h, to be confirmed).

## Options considered

1. One EU provider with VPS and managed PostgreSQL 17 with PITR in the same data centre: least
   database work, higher monthly cost, fewer providers to choose from.
2. VPS from one provider and managed PostgreSQL from another in the same city: two contracts,
   traffic across networks, more points of failure.
3. Hetzner Cloud VPS for both the app and the database, PostgreSQL self-managed on its own
   server, backups and PITR run by us: lowest cost and full control over the database version
   and extensions, at the price of database operations becoming our job.

## Decision

Option 3:

- Provider: Hetzner Cloud, EU region, Arm64 (aarch64) servers for STAGE and LIVE
  (amended 2026-11-08; the release image is multi-arch).
- LIVE has two VPS: an app VPS and a dedicated database VPS. STAGE has one VPS holding app,
  worker, Caddy and PostgreSQL (amended 2026-11-08).
- The app VPS runs app, worker and Caddy as rootless Podman Quadlet units, as built in ARC.5.
  Releases are image tags, and rollback means switching back to the previous tag.
- The database VPS runs PostgreSQL 17, self-managed. It is reachable only over the Hetzner
  private network: no public port, firewall rules allow the app VPS only, and connections use
  TLS. There is one database server per environment.
- Backups and PITR are ours: continuous WAL archiving plus scheduled full and differential base
  backups with pgBackRest, sent to Hetzner Object Storage (S3 compatible, EU), encrypted
  client-side, with a documented retention policy. The bucket is separate from media and its
  credentials are not available to the app.
- A restore test is part of OPS.1 and is repeated regularly: restore to an empty server at a
  chosen point in time, then check the application against it.
- Media go to Hetzner Object Storage (EU) behind the existing CDN, with private buckets for
  documents and signed URLs (ARC.15).
- Server names, addresses, accounts and keys are environment configuration and never go into
  the repository.

## Consequences

- There is still no compose file: Makefile targets drive the local stack and Quadlet units run
  the app VPS. The database VPS is set up by a versioned, repeatable procedure (ARC.13, OPS.1).
- Database operations become our work, and the monthly hosting bill is lower in exchange:
  - OS security patches on both VPS, and PostgreSQL minor updates, applied on a schedule with a
    short planned downtime, since there is a single database server.
  - Major PostgreSQL upgrades (17 to 18 and later) planned as projects, tested on STAGE first.
  - Monitoring of disk space, WAL archiving lag and failures, backup age, replication if added,
    and connection counts, with alerts (OPS.2). A stuck WAL archive fills the disk and stops
    the database, so this alert is mandatory.
  - Restore drills on a schedule; a backup that was never restored does not count.
- There is no automatic failover. A database server loss means a restore from Object Storage
  within the RTO. A streaming replica can be added later if the RTO has to shrink.
- ARC.5 closes on what is proven locally and in CI; provisioning and the first real deploy and
  rollback move to ARC.13.
- We revisit this decision if database operations take more time than the managed price
  difference is worth, or if the RTO must drop below what a restore can achieve.

## Amendment 2026-11-08

- **STAGE on one VPS (owner decision).** STAGE holds only synthetic data, so it runs app, worker,
  Caddy and PostgreSQL 17 on a single Hetzner Cloud VPS, at lower cost. PostgreSQL listens only
  on the loopback interface, and the app still connects with TLS `sslmode=verify-full` using the
  internal-CA certificate (owner decision MQ-11), so the LIVE connection path is exercised. LIVE
  keeps two VPS (app VPS and a dedicated database VPS on the Hetzner private network), unchanged.
  STAGE and LIVE never share a machine, keys or database: STAGE runs untested releases and
  destructive tests, LIVE holds personal and payment data, and each environment has its own
  secrets. App-to-database network failures cannot be tested on one VPS, so they are repeated on
  LIVE-equivalent servers before cutover (MIG.9).
- **Arm64 servers (lane-a technical decision, delegated by the owner).** STAGE and LIVE use
  Hetzner Arm64 (aarch64) servers instead of x86, so STAGE mirrors LIVE. The release image
  becomes multi-arch (linux/amd64 and linux/arm64), with the arm64 variant built on a native
  GitHub arm64 runner, without QEMU emulation; this CI change is part of ARC.13. Reasons: Arm
  servers cost less at the same size, the development Macs are arm64, and all Python
  dependencies ship arm64 wheels.
- STAGE abuse tests before go-live are tracked as ARC.21.
