from tests.conftest import auth_headers, create_payload


async def test_request_from_one_workspace_is_invisible_to_another(client):
    ws1_headers = auth_headers(workspace_id="ws_1")
    ws2_headers = auth_headers(workspace_id="ws_2")

    created = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=ws1_headers, json=create_payload()
    )
    request_id = created.json()["id"]

    # Even with a fully authenticated, permission-granted actor for ws_2,
    # a request that lives in ws_1 must not be reachable via ws_2's path.
    get_response = await client.get(
        f"/api/v1/workspaces/ws_2/approval-requests/{request_id}", headers=ws2_headers
    )
    assert get_response.status_code == 404

    list_response = await client.get("/api/v1/workspaces/ws_2/approval-requests", headers=ws2_headers)
    assert list_response.json()["items"] == []

    decide_response = await client.post(
        f"/api/v1/workspaces/ws_2/approval-requests/{request_id}/approve",
        headers=ws2_headers,
        json={"comment": "Approved"},
    )
    assert decide_response.status_code == 404


async def test_same_source_id_in_different_workspaces_does_not_conflict(client):
    payload = create_payload()

    ws1 = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=auth_headers(workspace_id="ws_1"), json=payload
    )
    ws2 = await client.post(
        "/api/v1/workspaces/ws_2/approval-requests", headers=auth_headers(workspace_id="ws_2"), json=payload
    )

    assert ws1.status_code == 201
    assert ws2.status_code == 201
