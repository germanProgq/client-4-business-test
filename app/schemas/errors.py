from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody

    @classmethod
    def build(cls, code: str, message: str, details: Optional[dict[str, Any]] = None) -> "ErrorResponse":
        return cls(error=ErrorBody(code=code, message=message, details=details or {}))
