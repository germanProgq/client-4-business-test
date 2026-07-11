from tests.conftest import auth_headers, create_payload


async def test_get_existing_request(client):
    headers = auth_headers()
    created = await client.post("/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload())
    request_id = created.json()["id"]

    response = await client.get(f"/api/v1/workspaces/ws_1/approval-requests/{request_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == request_id


async def test_get_missing_request_is_404(client):
    headers = auth_headers()
    missing_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/v1/workspaces/ws_1/approval-requests/{missing_id}", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_get_with_malformed_uuid_is_422(client):
    headers = auth_headers()
    response = await client.get("/api/v1/workspaces/ws_1/approval-requests/not-a-uuid", headers=headers)
    assert response.status_code == 422
