from sqlalchemy import select

from app.db.models import ApprovalAuditLog
from tests.conftest import auth_headers, create_payload


async def test_create_and_decide_write_audit_rows(client, sessionmaker):
    headers = auth_headers()
    created = await client.post(
        "/api/v1/workspaces/ws_1/approval-requests", headers=headers, json=create_payload()
    )
    request_id = created.json()["id"]

    await client.post(
        f"/api/v1/workspaces/ws_1/approval-requests/{request_id}/approve",
        headers=headers,
        json={"comment": "Approved"},
    )

    async with sessionmaker() as session:
        result = await session.execute(
            select(ApprovalAuditLog).order_by(ApprovalAuditLog.created_at)
        )
        rows = result.scalars().all()

    assert [row.action for row in rows] == ["create", "approve"]
    assert all(row.actor_id == "usr_1" for row in rows)
    assert all(row.workspace_id == "ws_1" for row in rows)

    create_row, approve_row = rows
    assert create_row.previous_state is None
    assert create_row.new_state["status"] == "pending"
    assert approve_row.previous_state["status"] == "pending"
    assert approve_row.new_state["status"] == "approved"
