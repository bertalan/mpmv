# ADR-0004: Background tasks and scheduler on PostgreSQL

- **Status:** Accepted
- **Date:** 2026-11-07
- **Deciders:** tech lead
- **Backlog:** ARC.2, ARC.9

## Context

We need background jobs (imports, emails, feeds, webhook follow-ups), cron-like schedules (stock and
price sync, nightly reconciliation, report mailers), retries, and locks that stop a job from
overlapping itself. Principle P3 rules out Redis and Celery. Both candidates run on PostgreSQL.

## Options considered

1. **django-tasks with its database backend**: implements Django's `django.tasks` API, which is in
   core since 6.0. It has no built-in periodic scheduling, so we would need a separate cron process,
   and locking is left to us.
2. **Procrastinate**: a mature PostgreSQL task queue with periodic tasks, queueing locks, retries
   with backoff, a Django integration and psycopg 3 support. It is actively released (3.10, 2026-09).

## Decision

Use **Procrastinate** for workers, periodic tasks and locks. Application code never imports it
directly. It calls `apps.core.tasks` (a thin wrapper: `enqueue`, `periodic`, `locked`), so a later
move to a `django.tasks` backend is a change in one module.

## Consequences

- One extra process type (the worker) in every environment, running the same image.
- Its job tables live in the application database, so they are covered by the same backups.
- Queue health (queue depth, failed jobs) becomes part of the OPS monitoring tasks.
