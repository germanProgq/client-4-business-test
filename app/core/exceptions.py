"""Domain exceptions mapped to HTTP responses by the handlers registered in
``app.main``. Handlers only ever see these types (or an unexpected
exception, which is logged and turned into a generic 500) so the error
envelope stays consistent across the API.
"""

from typing import Any, Optional


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class UnauthenticatedError(AppError):
    status_code = 401
    code = "unauthenticated"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class InvalidStateTransitionError(ConflictError):
    code = "invalid_state_transition"


class DuplicateActiveRequestError(ConflictError):
    code = "duplicate_active_request"


class IdempotencyKeyReuseError(AppError):
    status_code = 422
    code = "idempotency_key_reused"
