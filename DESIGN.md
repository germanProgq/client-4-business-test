# DESIGN.md

## Service boundaries

`approval-service` owns exactly one thing: the lifecycle of an approval request and the
decision made on it. It does not own publications, scenarios, users, or workspaces --
those are external entities that already exist in the product. They cross the boundary
only as opaque identifiers (`sourceType` + `sourceId`, `reviewerUserIds`, `workspace_id`,
the acting `user_id`). This service never calls out to fetch or validate them, never
stores their contents, and never resolves a `reviewerUserId` to a name or email.

Two consequences of that boundary:

- **No fan-out on write.** Creating an approval request does not notify reviewers,
  update the source publication's status, or call any other service synchronously.
  Anything else that needs to react to a decision does so by consuming the events this
  service emits (see below), not by this service reaching into their domain.
- **No enrichment on read.** `GET .../approval-requests` returns exactly what this
  service knows -- ids, title/description as given, status, timestamps. A UI wanting
  reviewer avatars or publication thumbnails composes that itself from the owning
  services, keyed by the ids this service returns.

## Data model

```
approval_requests
  id                  uuid pk
  workspace_id        varchar        -- tenant key, present on every row/index
  source_type         enum(publication, scenario, edit, external)
  source_id           varchar        -- external id, opaque
  title               varchar(500)
  description         text null
  status              enum(pending, approved, rejected, cancelled)
  version             int            -- optimistic lock
  created_by          varchar
  decided_by           varchar null
  decided_at           timestamptz null
  resolution_note      text null      -- approve comment / reject|cancel reason
  created_at / updated_at  timestamptz

approval_reviewers      (request_id, user_id)               -- pk on both columns
approval_audit_logs      (id, request_id, workspace_id, actor_id, action,
                          previous_state jsonb, new_state jsonb, created_at)
idempotency_keys         (id, workspace_id, idempotency_key, scope,
                          request_fingerprint, response_status_code,
                          response_body jsonb, created_at)
outbox_events            (id, workspace_id, request_id, event_type,
                          payload jsonb, created_at, published_at null)
```

Notes on specific choices:

- **UUID primary keys.** Non-enumerable, safe to generate client-side or across
  replicas without coordination. The trade-off (random UUIDv4 has poor btree locality
  and doesn't preserve insertion order -- see "Known trade-offs") is accepted for this
  scope rather than adding a UUIDv7/sequence dependency.
- **`workspace_id` on every table**, including `approval_audit_logs` and
  `outbox_events`, not just `approval_requests`. Every query in
  `app/services/approval_service.py` filters on it, and it's always taken from the
  authenticated context (`X-Workspace-Id`), never trusted from a client-supplied body
  field -- see "Multi-tenancy" below.
- **`version` + a DB-level `CHECK` constraint** back the state machine (next section).
- **`idempotency_keys` and `outbox_events` are separate tables**, not columns on
  `approval_requests`, because a single request can accumulate multiple idempotency
  records (one for its creation, one per decision attempt) and multiple outbox events
  over its lifetime -- a 1:1 column wouldn't fit either.

### Indexing

- `idx_requests_workspace_status (workspace_id, status, created_at)` -- serves
  `GET .../approval-requests?status=...` as one index scan: filter by tenant and status,
  already in the right order for the default (newest-first) sort.
- `idx_requests_workspace_created (workspace_id, created_at)` -- the same endpoint
  without a status filter needs its own index, since a composite index can't serve an
  `ORDER BY` on its third column when the second column (`status`) is unconstrained.
- `idx_requests_workspace_id_pk (workspace_id, id)` -- keeps `GET .../{request_id}`
  and the approve/reject/cancel lookups fully index-covered on the tenant + primary key
  pair, even though the isolation check itself lives in application code, not the index.
- `idx_unique_active_request` -- a **partial unique index** on
  `(workspace_id, source_type, source_id) WHERE status = 'pending'`. At most one active
  request per source entity; a second create attempt fails fast at the database level
  with `409 duplicate_active_request` instead of silently allowing parallel approvals
  for the same thing. Partial, not a full unique constraint, because *resolved*
  requests for the same source (e.g. rejected, then resubmitted) must be allowed to
  coexist with history.
- `idx_outbox_unpublished` -- partial index on `outbox_events (created_at) WHERE
  published_at IS NULL`, so the dispatcher's poll query never scans published history.

## Multi-tenancy

Isolation is enforced twice, independently:

1. **At the HTTP boundary** (`app/core/auth.py::enforce_workspace_match`): the
   `{workspace_id}` path parameter must equal the authenticated `X-Workspace-Id`, or the
   request is rejected with `403` before touching the database at all.
2. **At every query** (`app/services/approval_service.py`): every `SELECT`/`UPDATE`
   against `approval_requests` includes `workspace_id = :workspace_id` in its `WHERE`
   clause, sourced from the auth context. A request that belongs to workspace A is
   simply absent from any query scoped to workspace B -- it 404s, it doesn't 403,
   because as far as workspace B's queries are concerned it doesn't exist.

Layer 2 is the real guarantee; layer 1 is a cheap early rejection that also means a bug
in one single-tenant-looking code path can't silently leak data cross-tenant, because
the mismatch is caught before that code path is ever reached.

## RBAC

Four scopes -- `approval:read`, `approval:create`, `approval:decide`, `approval:cancel`
-- carried on `X-User-Permissions` (see README for the header contract). Each route
depends on `require_permission(scope)`, which resolves the auth context and checks
membership in one step; unknown scopes on the header are dropped rather than causing a
hard failure, so a forward-compatible client sending extra scopes doesn't break.

## State machine & concurrency

States: `pending -> {approved, rejected, cancelled}`. All three outcomes are final --
none of them transition anywhere else. Enforced twice:

- **Application layer:** any decide call first checks `status == pending`; anything
  else raises `409 invalid_state_transition` immediately.
- **Database layer, atomically:** the actual state change is a single conditional
  `UPDATE`:

  ```sql
  UPDATE approval_requests
  SET status = :new_status, version = version + 1, decided_by = :actor,
      decided_at = :now, resolution_note = :note, updated_at = :now
  WHERE id = :id AND workspace_id = :workspace_id
    AND status = 'pending' AND version = :expected_version;
  ```

  If two decide calls race (double-click Approve, or a retry overlapping the original),
  only the first `UPDATE` matches any rows. The second gets `rowcount == 0` and is told
  `409 invalid_state_transition` ("concurrently modified"), rather than silently
  clobbering the first decision or corrupting `version`. The optimistic-lock column
  (`version`) and the transition guard (`status = 'pending'`) are combined in one
  statement specifically so there's no window between "check" and "write" for another
  transaction to slip through -- no `SELECT ... FOR UPDATE` or app-level lock needed.
- **`CHECK (decision fields consistent with status)`** at the DB level: a row is either
  `pending` with no decision recorded, or final with `decided_by`/`decided_at` both set.
  This is independent of the application code path -- it catches a broken transition
  (e.g. a future bug that flips `status` without setting who decided) even if it slips
  past the service layer entirely.

## Handling duplicates / retries

Two distinct, complementary mechanisms, easy to conflate but solving different
problems:

- **Idempotency-Key** answers "did the client already send *this exact request*?" It's
  opt-in (client sends the header), scoped to `(workspace_id, key, operation)`, and
  stores the original response so a retry replays it verbatim rather than
  re-executing anything. Implemented as a unique constraint
  (`uq_idempotency_key_scope`) plus a lookup-before-write in
  `app/services/idempotency.py`; a genuine race between two concurrent requests
  carrying the same key is resolved by catching the constraint violation on commit and
  re-reading the winner's stored response (see `ApprovalService._replay_if_present`
  and its callers).
- **`idx_unique_active_request`** answers "does an active request already exist *for
  this source*, regardless of who's asking or what key they used?" It's not opt-in and
  doesn't require any client cooperation -- it's what stops two different idempotency
  keys (or a client that never sends one at all) from creating two parallel pending
  approvals for `pub_123`.

Every mutating service method (`create`, `approve`, `reject`, `cancel`) wraps its state
change, its audit-log row, its outbox event, and its idempotency record in one database
transaction. They all commit together or none do -- there's no window where a decision
is recorded but its audit trail isn't, or where an idempotency key is stored for a
change that never actually landed.

## Audit logging

Every successful mutation writes one `approval_audit_logs` row in the same transaction
as the change itself (`app/services/audit.py`): `actor_id`, `action`
(`create`/`approve`/`reject`/`cancel`), `previous_state`/`new_state` (the full resource
snapshot, sanitized -- see below), and `created_at`. This is intentionally append-only
and not exposed via any API endpoint in this assignment's scope (no
`GET .../audit-log` route) -- it exists as the durable "who changed what and when"
record a future admin/compliance view would read directly, not as a product feature
here.

## Event-driven integration

No real broker is wired up (the assignment explicitly asks not to add real external
services), but the service is structured so one is a drop-in, using the **transactional
outbox pattern**:

1. Every state change stages an `outbox_events` row (`app/services/outbox.py::stage`)
   in the *same transaction* as the change itself. This is the part that matters: the
   alternative -- committing the DB change, then separately calling a broker -- has a
   window where one succeeds and the other fails, leaving the two systems permanently
   inconsistent. Here, the event's existence is exactly as durable as the change it
   describes.
2. A background `OutboxDispatcher` polls `WHERE published_at IS NULL`, hands each row
   to an `EventPublisher`, and marks it published. Delivery is therefore
   **at-least-once**: if the process dies between publishing and marking the row
   published, the same event is sent again on restart. Any real consumer needs to be
   idempotent on `event_id` (or on the resource id + new status) -- this is the
   standard trade-off outbox dispatch makes, and it's the same shape of problem this
   service's own `Idempotency-Key` mechanism solves for its own clients.
3. `EventPublisher` is a two-method protocol (`app/services/events.py`). The shipped
   implementation (`LoggingEventPublisher`) just emits a sanitized structured log line.
   Swapping in Kafka/RabbitMQ means writing one class that implements `publish()` and
   passing it to `OutboxDispatcher` instead -- no other code changes.
4. Proposed topic/event naming for that future implementation: one topic per aggregate
   (e.g. `approval-requests.events`), `event_type` values
   `approval_request.created|approved|rejected|cancelled`, keyed by `workspace_id` (or
   `request_id`) for partition-level ordering per request.
5. The dispatcher's poll query uses `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres
   (conditionally -- SQLite, used only in tests, has no row locking and only ever runs
   single-instance), so running it from multiple API replicas concurrently -- which
   horizontal scaling implies -- never double-publishes the same row.

Today the dispatcher runs as a background `asyncio` task inside the API process
(started/stopped in `app/main.py`'s lifespan). At real scale it would move into its own
worker deployment so dispatch load never competes with request-handling for the API
process's event loop; the `SKIP LOCKED` design already makes that move safe with no
further changes.

## Data sanitization

`app/core/sanitize.py` recursively redacts any dict key matching a denylist
(password/secret/token/email/storageKey/signedUrl/providerPayload/... -- normalized to
be case- and separator-insensitive, so `provider_payload`, `providerPayload`, and
`provider-payload` are all caught by one marker). Applied at every point a structured
payload leaves the process boundary: the audit-log snapshot, the outbox event payload,
and the request-logging middleware's log line. This is defense-in-depth rather than the
primary control -- the primary control is that the domain model itself never has a
field for a secret, token, or raw provider payload to begin with (only opaque external
ids cross the boundary, per "Service boundaries" above).

## Horizontal scalability

- The API process is stateless: all state lives in Postgres, no in-memory session or
  sticky-routing requirement, so it scales by adding replicas behind a load balancer.
- Async I/O (FastAPI + asyncpg) means each replica handles many concurrent requests on
  one event loop without a thread per request.
- The outbox dispatcher is safe to run on every replica simultaneously (`SKIP LOCKED`),
  so scaling out the API doesn't require special-casing which replica "owns" dispatch.
- Every hot query is covered by an index scoped to `workspace_id` (see "Indexing"), so
  query cost doesn't grow with the number of *other* tenants as the dataset reaches
  "millions of users" scale -- it grows with one tenant's own history, which pagination
  already bounds.

## Known trade-offs

- **Tests run against SQLite, not Postgres.** Fast, no external dependency, and the
  ORM/service layer is exercised identically either way -- but SQLite can't validate
  the Postgres-specific parts of the migration (native `ENUM`, `JSONB`, partial
  indexes, `FOR UPDATE SKIP LOCKED`). Those were written using standard, well-documented
  SQLAlchemy/Alembic patterns and reviewed by hand, but a real `docker compose up` +
  `alembic upgrade head` run against Postgres is the actual validation step before
  trusting this in production.
- **UUIDv4 primary keys don't preserve insertion order.** The list endpoint's pagination
  tiebreaker (`created_at`, then `id`) can order same-timestamp rows arbitrarily under
  true write concurrency, since the `id` tiebreak is a random UUID. Timestamps are
  generated in Python at microsecond resolution specifically to make same-timestamp
  collisions rare, but a system with a stricter total-order requirement at very high
  write concurrency would use UUIDv7 or a monotonic sequence column instead.
  (This is also why `created_at`/`updated_at`/etc. are Python-side defaults rather than
  a DB `server_default`/`now()`: SQLite's `CURRENT_TIMESTAMP` only has 1-second
  resolution, which made this collision the common case, not the edge case, under
  SQLite. Postgres's `now()` has microsecond resolution and would have mostly masked
  the same underlying issue.)
- **Auth is a stub.** Headers, not verified tokens -- explicitly allowed by the
  assignment for local running, but not something to expose past an upstream
  gateway/BFF that has actually authenticated the caller.
- **No rate limiting, distributed tracing, or metrics export.** Structured JSON logs
  (`app/logging_config.py`) are the only observability primitive included; a real
  deployment would add OpenTelemetry tracing and a metrics endpoint, and rate limiting
  at the gateway layer.
- **The outbox dispatcher is in-process, not a separate worker.** Fine at this scale;
  called out explicitly above as the first thing to split out under real load.
- **No soft-delete/GDPR-erasure path.** Audit logs and idempotency records reference
  `actor_id`/`created_by` (opaque external user ids, not PII) indefinitely; a
  production system would need a retention/erasure policy for these tables, which is
  out of scope here.
