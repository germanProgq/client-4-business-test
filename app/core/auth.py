"""Auth stub for local/dev use.

There is no real identity provider in front of this service yet, so
identity and authorization are carried directly on the request as headers,
exactly as the assignment allows ("request must contain workspace, user,
and a list of actions"). In production these headers would be set by an
upstream gateway/BFF after verifying a real session (JWT/OIDC), not by the
caller directly -- the contract (workspace id, user id, permission scopes)
would stay the same, only the source of truth would change. See README.md
for the header format and examples.
"""

from dataclasses import dataclass
from typing import FrozenSet

from fastapi import Depends, Header

from app.core.exceptions import ForbiddenError, UnauthenticatedError

WORKSPACE_HEADER = "X-Workspace-Id"
USER_HEADER = "X-User-Id"
PERMISSIONS_HEADER = "X-User-Permissions"

ALL_PERMISSIONS = frozenset(
    {"approval:read", "approval:create", "approval:decide", "approval:cancel"}
)


@dataclass(frozen=True)
class AuthContext:
    workspace_id: str
    user_id: str
    permissions: FrozenSet[str]

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


async def get_auth_context(
    x_workspace_id: str = Header(default=None, alias=WORKSPACE_HEADER),
    x_user_id: str = Header(default=None, alias=USER_HEADER),
    x_user_permissions: str = Header(default="", alias=PERMISSIONS_HEADER),
) -> AuthContext:
    if not x_workspace_id or not x_workspace_id.strip():
        raise UnauthenticatedError(f"Missing required '{WORKSPACE_HEADER}' header")
    if not x_user_id or not x_user_id.strip():
        raise UnauthenticatedError(f"Missing required '{USER_HEADER}' header")

    raw_scopes = {scope.strip() for scope in x_user_permissions.split(",") if scope.strip()}
    # Unknown scopes are dropped rather than rejected outright, so that
    # forward-compatible clients sending extra scopes do not hard-fail.
    granted = raw_scopes & ALL_PERMISSIONS

    return AuthContext(
        workspace_id=x_workspace_id.strip(),
        user_id=x_user_id.strip(),
        permissions=frozenset(granted),
    )


def require_permission(permission: str):
    """Build a FastAPI dependency that resolves the auth context and
    enforces a single RBAC permission scope in one step."""

    async def _check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_permission(permission):
            raise ForbiddenError(
                f"Missing required permission '{permission}'",
                details={"requiredPermission": permission},
            )
        return auth

    return _check


def enforce_workspace_match(path_workspace_id: str, auth: AuthContext) -> None:
    if auth.workspace_id != path_workspace_id:
        raise ForbiddenError(
            "Auth context workspace does not match the requested workspace",
            details={"pathWorkspaceId": path_workspace_id},
        )
