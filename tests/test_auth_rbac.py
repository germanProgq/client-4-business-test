from tests.conftest import auth_headers, create_payload


async def test_missing_workspace_header_is_unauthenticated(client):
    headers = auth_headers()
    del headers["X-Workspace-Id"]
    response = await client.get("/api/v1/workspaces/ws_1/approval-requests", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_missing_user_header_is_unauthenticated(client):
    headers = auth_headers()
    del headers["X-User-Id"]
    response = await client.get("/api/v1/workspaces/ws_1/approval-requests", headers=headers)
    assert response.status_code == 401


async def test_missing_permission_is_forbidden(client):
    headers = auth_headers(permissions=["approval:read"])
    response = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload()
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_workspace_path_header_mismatch_is_forbidden(client):
    headers = auth_headers(workspace_id="ws_1")
    response = await client.get("/api/v1/workspaces/ws_2/approval-requests", headers=headers)
    assert response.status_code == 403


async def test_unknown_permission_scopes_are_ignored_not_rejected(client):
    headers = auth_headers(permissions=["approval:read", "some:unknown:scope"])
    response = await client.get("/api/v1/workspaces/ws_1/approval-requests", headers=headers)
    assert response.status_code == 200
