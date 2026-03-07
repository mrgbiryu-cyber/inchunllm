# -*- coding: utf-8 -*-
"""
Main FastAPI application for AIBizPlan backend.
"""
import sys

# [UTF-8] Ensure process-level UTF-8 encoding for stdout/stderr
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding is None or sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from contextlib import asynccontextmanager
import asyncio
import time
import uuid
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse, JSONResponse
from sqlalchemy import text
from structlog import get_logger

from app.core.config import settings
from app.core.database import AsyncSessionLocal, init_db
from app.core.logging_config import setup_logging
from app.core.neo4j_client import neo4j_client
from app.services.job_manager import JobManager
from app.services.knowledge_service import knowledge_worker

# Setup logging before any other imports that might use it
setup_logging()
logger = get_logger(__name__)


class _InMemoryFallbackRedis:
    """Lightweight async redis-compatible stub for startup/health smoke checks."""

    async def ping(self) -> str:
        return "PONG"

    async def close(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup and shutdown events
    """
    logger.info("Starting AIBizPlan backend", version=settings.APP_VERSION)

    # Initialize RDB
    try:
        await init_db()
        logger.info("RDB initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize RDB", error=str(e))
        raise

    # Initialize Redis connection
    allow_startup_without_redis = bool(settings.STARTUP_WITHOUT_REDIS)
    redis_url = (settings.REDIS_URL or "").strip()

    if settings.ENVIRONMENT.lower() != "production" and not redis_url and not allow_startup_without_redis:
        # Safe local fallback for development only when startup requires redis.
        redis_url = "redis://localhost:6379/0"

    if not redis_url:
        if not allow_startup_without_redis:
            raise RuntimeError("REDIS_URL is required unless STARTUP_WITHOUT_REDIS=true.")
        redis_client = _InMemoryFallbackRedis()
        logger.warning("REDIS_URL missing; running with in-memory redis fallback")
    else:
        redis_client = redis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

    if settings.ENVIRONMENT.lower() == "production":
        if _is_loopback_endpoint(redis_url) and not allow_startup_without_redis:
            raise RuntimeError("REDIS_URL must not use localhost/127.0.0.1 in production.")
        if _is_loopback_endpoint(settings.NEO4J_URI or ""):
            raise RuntimeError("NEO4J_URI must not use localhost/127.0.0.1 in production.")

    if not isinstance(redis_client, _InMemoryFallbackRedis):
        try:
            await redis_client.ping()
            logger.info("Redis connection established", url=redis_url)
        except Exception as e:
            logger.error("Failed to connect to Redis", error=str(e))
            if not allow_startup_without_redis:
                raise
            logger.warning(
                "Redis unavailable; running in startup-degraded mode",
                fallback=True,
            )
            redis_client = _InMemoryFallbackRedis()

    # Initialize Job Manager
    job_manager = JobManager(redis_client)

    # Start Knowledge Worker
    worker_task = asyncio.create_task(knowledge_worker())

    # Store in app state
    app.state.redis = redis_client
    app.state.redis_is_fallback = isinstance(redis_client, _InMemoryFallbackRedis)
    app.state.job_manager = job_manager
    app.state.knowledge_worker = worker_task

    logger.info("Application startup complete")
    yield

    # Shutdown
    logger.info("Shutting down application")
    if worker_task:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass
    await redis_client.close()
    logger.info("Redis connection closed")


def _parse_cors_origins(origins: str) -> list[str]:
    return [origin.strip() for origin in origins.split(",") if origin.strip()]


def _is_loopback_endpoint(url_value: str) -> bool:
    lowered = (url_value or "").lower()
    return ("localhost" in lowered) or ("127.0.0.1" in lowered)


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=f"{settings.APP_TAGLINE}",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,
)


def _error_payload(error_code: str, message: str, detail, trace_id: str) -> dict:
    return {
        "error_code": error_code,
        "message": message,
        "detail": detail,
        "trace_id": trace_id,
    }


@app.middleware("http")
async def request_trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
    request.state.trace_id = trace_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = trace_id
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)
    detail = exc.detail
    if isinstance(detail, dict):
        error_code = detail.get("error_code", "HTTP_ERROR")
        message = detail.get("message", "Request failed")
        extra = {k: v for k, v in detail.items() if k not in {"error_code", "message"}}
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-Request-Id": trace_id},
            content=_error_payload(error_code, message, extra, trace_id),
        )
    message = detail if isinstance(detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        headers={"X-Request-Id": trace_id},
        content=_error_payload("HTTP_ERROR", message, detail, trace_id),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)
    return JSONResponse(
        status_code=422,
        headers={"X-Request-Id": trace_id},
        content=_error_payload("VALIDATION_ERROR", "Request validation failed", exc.errors(), trace_id),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    trace_id = getattr(request.state, "trace_id", uuid.uuid4().hex)
    logger.error("Unhandled exception", error=str(exc), trace_id=trace_id)
    detail = str(exc) if settings.ENVIRONMENT.lower() != "production" else None
    return JSONResponse(
        status_code=500,
        headers={"X-Request-Id": trace_id},
        content=_error_payload("INTERNAL_ERROR", "Internal server error", detail, trace_id),
    )


# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(settings.CORS_ORIGINS),
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)

# Include routers
from app.api.v1 import admin, agents, auth, jobs, models, orchestration, projects, workers

app.include_router(auth.router, prefix="/api/v1")
app.include_router(jobs.router, prefix="/api/v1")
app.include_router(workers.router, prefix="/api/v1")
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(projects.router, prefix="/api/v1/projects", tags=["projects"])
app.include_router(agents.router, prefix="/api/v1", tags=["agents"])
app.include_router(orchestration.router, prefix="/api/v1/orchestration", tags=["orchestration"])
app.include_router(models.router, prefix="/api/v1/models", tags=["models"])

from app.api.v1 import files

app.include_router(files.router, prefix="/api/v1", tags=["files"])

from app.api.v1 import master

app.include_router(master.router, prefix="/api/v1/master", tags=["master"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "operational",
        "environment": settings.ENVIRONMENT,
    }


def _elapsed_ms(start_ts: float) -> int:
    return int((time.perf_counter() - start_ts) * 1000)


async def _check_redis(redis_timeout_sec: float) -> dict:
    start_ts = time.perf_counter()
    if getattr(app.state, "redis_is_fallback", False):
        return {
            "status": "degraded",
            "latency_ms": _elapsed_ms(start_ts),
            "error": "in-memory redis fallback is active",
        }
    try:
        await asyncio.wait_for(app.state.redis.ping(), timeout=redis_timeout_sec)
        return {"status": "healthy", "latency_ms": _elapsed_ms(start_ts)}
    except Exception as e:
        logger.warning("Redis health check failed", error=str(e))
        return {"status": "unhealthy", "latency_ms": _elapsed_ms(start_ts), "error": str(e)}


async def _check_postgresql(pg_timeout_sec: float) -> dict:
    start_ts = time.perf_counter()
    try:
        async with AsyncSessionLocal() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=pg_timeout_sec)
        return {"status": "healthy", "latency_ms": _elapsed_ms(start_ts)}
    except Exception as e:
        logger.warning("PostgreSQL health check failed", error=str(e))
        return {"status": "unhealthy", "latency_ms": _elapsed_ms(start_ts), "error": str(e)}


async def _check_neo4j(neo4j_timeout_sec: float) -> dict:
    start_ts = time.perf_counter()
    try:
        connected = await asyncio.wait_for(
            neo4j_client.verify_connectivity(),
            timeout=neo4j_timeout_sec,
        )
        return {
            "status": "healthy" if connected else "unhealthy",
            "latency_ms": _elapsed_ms(start_ts),
        }
    except Exception as e:
        logger.warning("Neo4j health check failed", error=str(e))
        return {"status": "unhealthy", "latency_ms": _elapsed_ms(start_ts), "error": str(e)}


@app.get("/health")
async def health_check():
    """Composite health check endpoint for Redis, PostgreSQL, and Neo4j."""
    redis_timeout_sec = settings.HEALTH_REDIS_TIMEOUT_MS / 1000
    pg_timeout_sec = settings.HEALTH_POSTGRES_TIMEOUT_MS / 1000
    neo4j_timeout_sec = settings.HEALTH_NEO4J_TIMEOUT_MS / 1000

    redis_component = await _check_redis(redis_timeout_sec)
    pg_component = await _check_postgresql(pg_timeout_sec)
    neo4j_component = await _check_neo4j(neo4j_timeout_sec)

    component_statuses = [
        redis_component["status"],
        pg_component["status"],
        neo4j_component["status"],
    ]
    if all(status == "healthy" for status in component_statuses):
        overall_status = "healthy"
    elif all(status == "unhealthy" for status in component_statuses):
        overall_status = "unhealthy"
    else:
        overall_status = "degraded"

    return {
        "status": overall_status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": {
            "redis": redis_component,
            "postgresql": pg_component,
            "neo4j": neo4j_component,
        },
    }


@app.get("/api/v1/health")
async def api_health_check():
    """API V1 health check endpoint (alias)."""
    return await health_check()


@app.get("/api/v1/admin/health")
async def admin_api_health_check():
    """Admin API health endpoint for deployment checks."""
    return {"status": "healthy", "service": "buja-admin-api", "checked_at": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
