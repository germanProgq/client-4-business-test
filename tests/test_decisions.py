from tests.conftest import auth_headers, create_payload


async def _create(client, headers, **overrides):
    response = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload(**overrides)
    )
    assert response.status_code == 201
    return response.json()


async def test_approve_pending_request(client):
    headers = auth_headers()
    created = await _create(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/approve",
        headers=headers,
        json={"comment": "Approved"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert body["decidedBy"] == "usr_1"
    assert body["decidedAt"] is not None
    assert body["resolutionNote"] == "Approved"
    assert body["version"] == 2


async def test_approve_with_empty_body_uses_no_comment(client):
    headers = auth_headers()
    created = await _create(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/approve", headers=headers, json={}
    )
    assert response.status_code == 200
    assert response.json()["resolutionNote"] is None


async def test_reject_requires_reason(client):
    headers = auth_headers()
    created = await _create(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/reject", headers=headers, json={}
    )
    assert response.status_code == 422


async def test_reject_pending_request(client):
    headers = auth_headers()
    created = await _create(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/reject",
        headers=headers,
        json={"reason": "Brand tone is wrong"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["resolutionNote"] == "Brand tone is wrong"


async def test_cancel_pending_request(client):
    headers = auth_headers()
    created = await _create(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/cancel",
        headers=headers,
        json={"reason": "Draft was removed"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def test_cannot_decide_twice(client):
    headers = auth_headers()
    created = await _create(client, headers)

    first = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/approve",
        headers=headers,
        json={"comment": "Approved"},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/reject",
        headers=headers,
        json={"reason": "Changed my mind"},
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "invalid_state_transition"


async def test_cancel_after_reject_is_conflict(client):
    headers = auth_headers()
    created = await _create(client, headers)

    await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/reject",
        headers=headers,
        json={"reason": "Brand tone is wrong"},
    )
    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/cancel",
        headers=headers,
        json={"reason": "Draft was removed"},
    )
    assert response.status_code == 409


async def test_decide_on_missing_request_is_404(client):
    headers = auth_headers()
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{missing_id}/approve",
        headers=headers,
        json={"comment": "Approved"},
    )
    assert response.status_code == 404


async def test_cancel_requires_cancel_permission(client):
    headers = auth_headers(permissions=["approval:read", "approval:create", "approval:decide"])
    created = await _create(client, headers)

    response = await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{created['id']}/cancel",
        headers=headers,
        json={"reason": "Draft was removed"},
    )
    assert response.status_code == 403
