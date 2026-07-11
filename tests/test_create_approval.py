from tests.conftest import auth_headers, create_payload


async def test_create_approval_request_success(client):
    response = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=auth_headers(), json=create_payload()
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["workspaceId"] == "ws_1"
    assert body["sourceType"] == "publication"
    assert body["sourceId"] == "pub_123"
    assert body["reviewerUserIds"] == ["usr_1", "usr_2"]
    assert body["createdBy"] == "usr_1"
    assert body["decidedBy"] is None
    assert body["version"] == 1


async def test_create_rejects_invalid_source_type(client):
    response = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers=auth_headers(),
        json=create_payload(sourceType="not_a_real_type"),
    )
    assert response.status_code == 422


async def test_create_rejects_blank_title(client):
    response = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=auth_headers(), json=create_payload(title="   ")
    )
    assert response.status_code == 422


async def test_create_dedupes_reviewer_ids(client):
    response = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers=auth_headers(),
        json=create_payload(reviewerUserIds=["usr_1", "usr_1", "usr_2", ""]),
    )
    assert response.status_code == 201
    assert response.json()["reviewerUserIds"] == ["usr_1", "usr_2"]


async def test_second_pending_request_for_same_source_conflicts(client):
    headers = auth_headers()
    first = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload()
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload(title="Different title")
    )
    assert second.status_code == 409
    body = second.json()
    assert body["error"]["code"] == "duplicate_active_request"
    assert body["error"]["details"]["existingRequestId"] == first_id
