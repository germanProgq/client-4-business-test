async def test_liveness_ok(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_ok(client):
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
