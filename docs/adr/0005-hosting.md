# ADR-0005: Hosting model

- **Status:** Superseded by ADR-0007
- **Date:** 2026-11-07
- **Deciders:** project owner, tech lead
- **Backlog:** ARC.5 · **Closes:** D-12

## Context

The rebuild exists to cut operating effort. We need EU data residency (GDPR), backups with
point-in-time recovery, and the ability to run the same OCI images we build locally
([ADR-0002](0002-local-containers-apple.md)).

## Options considered

1. **Self-managed VPS with self-managed PostgreSQL**: cheapest, but backups, upgrades and
   failover become our job.
2. **EU VPS for the app plus managed PostgreSQL in the EU**: we run stateless containers, and the
   provider handles database backups, PITR and minor upgrades.
3. **EU PaaS**: least operations, but more expensive at steady load, with less control over workers
   and cron.

## Decision

Option 2:

- STAGE and LIVE each run on an EU VPS: app, worker and Caddy as OCI containers under **Podman with
  systemd Quadlet units** (daemonless and rootless, restarted by systemd). Releases are image tags,
  and rollback means switching back to the previous tag.
- **Managed PostgreSQL 17** in the same EU region, with PITR enabled. There is a separate database
  per environment.
- S3-compatible EU object storage for media, behind the existing CDN.
- The provider is chosen in ARC.5 by price, EU region and managed PostgreSQL availability. The
  choice is recorded in that task's PR, not here.

## Consequences

- There is no compose file anywhere: Makefile targets drive the local stack and Quadlet units run
  the servers.
- Database operations (backup restores, version upgrades) follow provider procedures. OPS tasks
  still test a restore regularly.
