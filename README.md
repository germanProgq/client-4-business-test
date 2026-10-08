# approval-service

A backend service for content-approval workflows. You create an approval request for a
publication, scenario, edit, or external item, route it to reviewers, and record a final
Approve, Reject, or Cancel decision with a full audit trail.

This is a test task written for the clients4business interview. It is a self-contained
exercise, not a production deployment.

Built with FastAPI (async), PostgreSQL with SQLAlchemy 2.0 and Alembic, and a stateless,
horizontally scalable design. See [DESIGN.md](DESIGN.md) for the data model, service
boundaries, and the reasoning behind the concurrency, idempotency, and eventing choices.

## Contents

- [Quickstart (Docker)](#quickstart-docker)
- [Local development (without Docker)](#local-development-without-docker)
- [Auth model](#auth-model)
- [API](#api)
- [Idempotency](#idempotency)
- [Errors](#errors)
- [Tests](#tests)
- [Configuration](#configuration)
- [Project layout](#project-layout)

## Quickstart (Docker)

Requires Docker and Docker Compose.

```bash
docker compose up --build
```

This starts Postgres, runs `alembic upgrade head` via a one-shot `migrate` service, and
then starts the API on `http://localhost:8000`. Interactive API docs are at
`http://localhost:8000/docs`.

Run the test suite in a container (uses an isolated in-memory SQLite database, so no
Postgres is required):

```bash
docker compose --profile test run --rm tests
```

## Local development (without Docker)

Requires Python 3.12+ and a running PostgreSQL instance (or adjust `DATABASE_URL` to
point at any Postgres you have).

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
# source .venv/bin/activate   # macOS/Linux

pip install -r requirements-dev.txt
cp .env.example .env          # edit DATABASE_URL / DATABASE_URL_SYNC if needed

alembic upgrade head
uvicorn app.main:app --reload
```

## Auth model

There is no real identity provider for this assignment, so the service uses an explicit
auth stub. The caller (in production, an upstream gateway or BFF that has already
verified a real session) sets three headers describing who is making the request and
what they are allowed to do:

| Header                 | Meaning                                                      |
|------------------------|--------------------------------------------------------------|
| `X-Workspace-Id`       | Tenant the request acts on behalf of                         |
| `X-User-Id`            | Acting user (recorded as `createdBy` / `decidedBy` and in audit logs) |
| `X-User-Permissions`   | Comma-separated permission scopes granted to this user       |

Recognized scopes: `approval:read`, `approval:create`, `approval:decide`,
`approval:cancel` (`decide` covers both approve and reject). Missing `X-Workspace-Id` or
`X-User-Id` returns `401`. A missing required scope returns `403`. The `workspace_id` in
the URL path must match `X-Workspace-Id` exactly, or the request is rejected with `403`
before any database lookup. That is a second, independent layer of tenant isolation on
top of every query already filtering by workspace internally (see DESIGN.md).

Swapping in real auth later only means replacing how these three values are derived
(`app/core/auth.py`). Everything downstream already consumes them as opaque inputs.

## API

| Method | Path                                                                        | Scope             |
|--------|-----------------------------------------------------------------------------|-------------------|
| POST   | `/api/v1/workspaces/{workspace_id}/approval-requests`                       | `approval:create` |
| GET    | `/api/v1/workspaces/{workspace_id}/approval-requests`                       | `approval:read`   |
| GET    | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}`          | `approval:read`   |
| POST   | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/approve`  | `approval:decide` |
| POST   | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/reject`   | `approval:decide` |
| POST   | `/api/v1/workspaces/{workspace_id}/approval-requests/{request_id}/cancel`   | `approval:cancel` |

### Create a request

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests \
  -H "X-Workspace-Id: ws_1" -H "X-User-Id: usr_1" \
  -H "X-User-Permissions: approval:create" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 3f6a9e7e-8f34-4c9a-9c8a-2e6a2f6b7a11" \
  -d '{
        "sourceType": "publication",
        "sourceId": "pub_123",
        "title": "Instagram reel draft",
        "description": "Needs final approval",
        "reviewerUserIds": ["usr_1", "usr_2"]
      }'
```

### List requests in a workspace

```bash
curl "http://localhost:8000/api/v1/workspaces/ws_1/approval-requests?status=pending&limit=20" \
  -H "X-Workspace-Id: ws_1" -H "X-User-Id: usr_1" -H "X-User-Permissions: approval:read"
```

Supports `status`, `sourceType`, `limit` (default 20, max 100), and `cursor` (opaque,
returned as `nextCursor`, pass it back to get the next page) query params.

### Approve, reject, cancel

```bash
curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/<id>/approve \
  -H "X-Workspace-Id: ws_1" -H "X-User-Id: usr_2" -H "X-User-Permissions: approval:decide" \
  -H "Content-Type: application/json" -d '{"comment": "Approved"}'

curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/<id>/reject \
  -H "X-Workspace-Id: ws_1" -H "X-User-Id: usr_2" -H "X-User-Permissions: approval:decide" \
  -H "Content-Type: application/json" -d '{"reason": "Brand tone is wrong"}'

curl -X POST http://localhost:8000/api/v1/workspaces/ws_1/approval-requests/<id>/cancel \
  -H "X-Workspace-Id: ws_1" -H "X-User-Id: usr_1" -H "X-User-Permissions: approval:cancel" \
  -H "Content-Type: application/json" -d '{"reason": "Draft was removed"}'
```

A request already in a final state (`approved`, `rejected`, `cancelled`) cannot be
decided again. Any further approve, reject, or cancel call returns
`409 invalid_state_transition`.

## Idempotency

Every mutating endpoint (create, approve, reject, cancel) accepts an optional
`Idempotency-Key` header. Replaying the same key with the same request body returns the
original response verbatim (same status code, no new record created or state re-applied).
Reusing a key with a different body is rejected with `422 idempotency_key_reused`. Keys
are scoped per workspace and per operation, so the same key value can be reused safely
across different endpoints or different workspaces.

Independent of idempotency keys, creating a second request for a `sourceType` and
`sourceId` that already has a pending request fails with `409 duplicate_active_request`
(the response includes `existingRequestId`). This guards against duplicate approvals
piling up even when a client does not send a key at all.

## Errors

All errors share one envelope:

```json
{ "error": { "code": "invalid_state_transition", "message": "...", "details": { } } }
```

| Status | `code`                     | When                                                        |
|--------|----------------------------|-------------------------------------------------------------|
| 400    | `bad_request`              | Malformed pagination cursor                                 |
| 401    | `unauthenticated`          | Missing `X-Workspace-Id` or `X-User-Id`                     |
| 403    | `forbidden`                | Missing permission scope, or path/header workspace mismatch |
| 404    | `not_found`                | Request does not exist in this workspace                    |
| 409    | `invalid_state_transition` | Decision on a non-pending request, or lost a concurrent-decision race |
| 409    | `duplicate_active_request` | A pending request already exists for this source            |
| 422    | `validation_error`         | Request body failed schema validation                       |
| 422    | `idempotency_key_reused`   | Same `Idempotency-Key`, different request body              |
| 500    | `internal_error`           | Unexpected server error                                     |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests run against an isolated in-memory SQLite database per test (see
`tests/conftest.py`) through the same FastAPI app used in production, so the full HTTP
stack (routing, auth, RBAC, validation, exception handling) is exercised, not just the
service layer.

## Configuration

Environment variables (see `.env.example`):

| Variable                       | Default                                                        | Purpose |
|--------------------------------|----------------------------------------------------------------|---------|
| `DATABASE_URL`                 | `postgresql+asyncpg://approval:approval@localhost:5432/approval_service` | App runtime DSN (async) |
| `DATABASE_URL_SYNC`            | derived from `DATABASE_URL`                                     | Alembic migration DSN (sync, psycopg2) |
| `LOG_LEVEL`                    | `INFO`                                                          | Log verbosity |
| `OUTBOX_POLL_INTERVAL_SECONDS` | `2.0`                                                          | Outbox dispatcher poll interval |

## Project layout

```
app/
  api/        HTTP layer: routers, request/response wiring, DI
  core/       Auth stub, RBAC, exceptions, redaction, pagination
  db/         SQLAlchemy models, session/engine setup
  schemas/    Pydantic request/response models
  services/   Business logic: state machine, idempotency, audit, outbox/events
  main.py     App factory, middleware, exception handlers
alembic/      Database migrations
tests/        pytest suite (SQLite-backed, full HTTP stack)
```
