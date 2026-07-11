import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import health
from app.api.v1.router import router as v1_router
from app.config import get_settings
from app.core.exceptions import AppError
from app.db.session import AsyncSessionLocal, get_db_session
from app.logging_config import configure_logging, log_event
from app.schemas.errors import ErrorResponse
from app.services.events import LoggingEventPublisher
from app.services.outbox import OutboxDispatcher

logger = logging.getLogger("approval_service.http")


def _safe_validation_errors(exc: RequestValidationError) -> list[dict]:
    safe = []
    for error in exc.errors():
        error = dict(error)
        # `ctx` can hold raw exception objects from custom validators; not
        # JSON-serializable and not useful to a client.
        error.pop("ctx", None)
        safe.append(error)
    return safe


def create_app(
    *,
    sessionmaker: Optional[async_sessionmaker] = None,
    start_outbox_dispatcher: bool = True,
) -> FastAPI:
    """App factory. Tests pass their own `sessionmaker` (SQLite-backed) and
    typically disable the outbox dispatcher, since it is covered by its own
    focused unit test rather than exercised through the HTTP layer."""

    settings = get_settings()
    configure_logging(settings.log_level)

    active_sessionmaker = sessionmaker or AsyncSessionLocal

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        dispatcher = OutboxDispatcher(
            active_sessionmaker,
            LoggingEventPublisher(),
            poll_interval_seconds=settings.outbox_poll_interval_seconds,
        )
        if start_outbox_dispatcher:
            dispatcher.start()
        try:
            yield
        finally:
            if start_outbox_dispatcher:
                await dispatcher.stop()

    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

    if sessionmaker is not None:

        async def _override_get_db_session():
            async with active_sessionmaker() as session:
                yield session

        app.dependency_overrides[get_db_session] = _override_get_db_session

    app.include_router(health.router)
    app.include_router(v1_router, prefix="/api/v1")

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        log_event(
            logger,
            logging.INFO,
            "http.request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
            workspace_id=request.headers.get("X-Workspace-Id"),
        )
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse.build(exc.code, exc.message, exc.details).model_dump(mode="json"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ErrorResponse.build(
                "validation_error", "Request failed validation", {"errors": _safe_validation_errors(exc)}
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        log_event(logger, logging.ERROR, "unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=500,
            content=ErrorResponse.build("internal_error", "An unexpected error occurred").model_dump(mode="json"),
        )

    return app


app = create_app()
