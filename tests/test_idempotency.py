from tests.conftest import auth_headers, create_payload


async def test_repeated_create_with_same_key_returns_same_record(client):
    headers = {**auth_headers(), "Idempotency-Key": "create-key-1"}
    payload = create_payload()

    first = await client.post("/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=payload)
    second = await client.post("/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listed = await client.get("/api/v1/workspaces/ws_1/approval-requests", headers=auth_headers())
    assert len(listed.json()["items"]) == 1


async def test_same_key_different_body_is_rejected(client):
    headers = {**auth_headers(), "Idempotency-Key": "create-key-2"}

    first = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload(sourceId="pub_a")
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload(sourceId="pub_b")
    )
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "idempotency_key_reused"


async def test_idempotency_key_is_scoped_per_workspace(client):
    payload = create_payload()
    key = "shared-key"

    ws1 = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers={**auth_headers(workspace_id="ws_1"), "Idempotency-Key": key},
        json=payload,
    )
    ws2 = await client.post(
        "/api/v1/workspaces/ws_2/approval-requests",
        headers={**auth_headers(workspace_id="ws_2"), "Idempotency-Key": key},
        json=payload,
    )

    assert ws1.status_code == 201
    assert ws2.status_code == 201
    assert ws1.json()["id"] != ws2.json()["id"]


async def test_repeated_decision_with_same_key_does_not_conflict(client):
    headers = auth_headers()
    created = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload()
    )
    request_id = created.json()["id"]

    decide_headers = {**headers, "Idempotency-Key": "approve-key-1"}
    payload = {"comment": "Approved"}

    first = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{request_id}/approve", headers=decide_headers, json=payload
    )
    second = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{request_id}/approve", headers=decide_headers, json=payload
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    # Without a repeated Idempotency-Key, a genuine second decision on an
    # already-decided request is a real conflict, not a silent replay.
    third = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{request_id}/approve",
        headers=headers,
        json={"comment": "Approved again"},
    )
    assert third.status_code == 409
