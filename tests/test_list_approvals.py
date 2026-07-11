from tests.conftest import auth_headers, create_payload


async def test_list_orders_newest_first(client):
    headers = auth_headers()
    for i in range(3):
        await client.post(
            "/api/v1/workspaces/ws_1/approval-requests",
            headers=headers,
            json=create_payload(sourceId=f"pub_{i}"),
        )

    response = await client.get("/api/v1/workspaces/ws_1/approval-requests", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    source_ids = [item["sourceId"] for item in body["items"]]
    assert source_ids == ["pub_2", "pub_1", "pub_0"]


async def test_list_filters_by_status(client):
    headers = auth_headers()
    pending = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload(sourceId="pub_a")
    )
    approved = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload(sourceId="pub_b")
    )
    await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{approved.json()['id']}/approve",
        headers=headers,
        json={"comment": "looks good"},
    )

    response = await client.get(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, params={"status": "pending"}
    )
    body = response.json()
    assert [item["id"] for item in body["items"]] == [pending.json()["id"]]


async def test_list_filters_by_source_type(client):
    headers = auth_headers()
    await client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers=headers,
        json=create_payload(sourceType="publication", sourceId="pub_a"),
    )
    await client.post(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers=headers,
        json=create_payload(sourceType="scenario", sourceId="scn_a"),
    )

    response = await client.get(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, params={"sourceType": "scenario"}
    )
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["sourceType"] == "scenario"


async def test_list_paginates_with_cursor(client):
    headers = auth_headers()
    for i in range(5):
        await client.post(
            "/api/v1/workspaces/ws_1/approval-requests",
            headers=headers,
            json=create_payload(sourceId=f"pub_{i}"),
        )

    first_page = await client.get(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, params={"limit": 2}
    )
    first_body = first_page.json()
    assert len(first_body["items"]) == 2
    assert first_body["nextCursor"] is not None

    second_page = await client.get(
        "/api/v1/workspaces/ws_1/approval-requests",
        headers=headers,
        params={"limit": 2, "cursor": first_body["nextCursor"]},
    )
    second_body = second_page.json()
    assert len(second_body["items"]) == 2

    first_ids = {item["id"] for item in first_body["items"]}
    second_ids = {item["id"] for item in second_body["items"]}
    assert first_ids.isdisjoint(second_ids)


async def test_list_rejects_malformed_cursor(client):
    headers = auth_headers()
    response = await client.get(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, params={"cursor": "not-a-valid-cursor"}
    )
    assert response.status_code == 400
