# -*- coding: utf-8 -*-
import asyncio
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, DateTime, Text, JSON, Integer, Float, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
import uuid
from typing import Tuple, Optional, List
from datetime import datetime, timezone
import os
import importlib.util
import re
import sys
from app.core.config import settings
from structlog import get_logger

# For SQLite compatibility with UUID-like strings if not using PostgreSQL
from sqlalchemy.pool import NullPool
from sqlalchemy.types import TypeDecorator, CHAR
import json

logger = get_logger(__name__)

class GUID(TypeDecorator):
    """Platform-independent GUID type.
    Uses PostgreSQL's UUID type, otherwise uses CHAR(32), storing as string without dashes.
    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID
            return dialect.type_descriptor(PG_UUID())
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        else:
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        else:
            try:
                return uuid.UUID(str(value))
            except ValueError:
                # Fallback for non-UUID strings like 'system-master'
                return value

Base = declarative_base()


def _naive_utcnow() -> datetime:
    """Return timezone-naive UTC timestamp for PostgreSQL timestamp without timezone columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class MessageModel(Base):
    __tablename__ = "messages"

    message_id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id = Column(GUID(), nullable=True)
    thread_id = Column(String, nullable=True) # Optional grouping
    sender_role = Column(String, nullable=False) # user | master | agent | tool | auditor
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=_naive_utcnow)
    metadata_json = Column(JSON, nullable=True)

class CostLogModel(Base):
    __tablename__ = "cost_logs"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id = Column(GUID(), nullable=True)
    message_id = Column(GUID(), nullable=True, unique=True) # Ensure Idempotency
    extraction_type = Column(String) # realtime | batch
    model_tier = Column(String) # high | low
    model_name = Column(String)
    tokens_in = Column(Integer, default=0)
    tokens_out = Column(Integer, default=0)
    estimated_cost = Column(Float, default=0.0)
    status = Column(String) # success | skip | fail
    timestamp = Column(DateTime, default=_naive_utcnow)

# [v3.2] Draft Model Definition (Fix for AsyncEngine inspection)
class DraftModel(Base):
    __tablename__ = "drafts"
    
    id = Column(String(255), primary_key=True)
    session_id = Column(String(255), nullable=False, index=True)
    user_id = Column(String(255), nullable=False)
    project_id = Column(String(255), nullable=True)
    status = Column(String(50), nullable=False, default='UNVERIFIED', index=True)
    category = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String(50), default='USER_UTTERANCE')
    timestamp = Column(DateTime, default=_naive_utcnow, index=True)
    ttl_days = Column(Integer, default=7)

class UserModel(Base):
    __tablename__ = "users"

    id = Column(String(50), primary_key=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    tenant_id = Column(String(50), nullable=False)
    role = Column(String(20), nullable=False)
    is_active = Column(Integer, default=1) # 1: True, 0: False (SQLite compat)
    created_at = Column(DateTime, default=_naive_utcnow)

class UserProjectModel(Base):
    __tablename__ = "user_projects"

    user_id = Column(String(50), primary_key=True)
    project_id = Column(String(50), primary_key=True)
    role = Column(String(20), default="viewer") # viewer | editor
    assigned_at = Column(DateTime, default=_naive_utcnow)

class ThreadModel(Base):
    __tablename__ = "threads"

    id = Column(String(100), primary_key=True)
    project_id = Column(GUID(), nullable=True) # Matches MessageModel.project_id type
    owner_user_id = Column(String(50), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_naive_utcnow)
    updated_at = Column(
        DateTime,
        default=_naive_utcnow,
        onupdate=_naive_utcnow,
    )


class GrowthRunModel(Base):
    __tablename__ = "growth_runs"

    id = Column(String(100), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_key = Column(String(100), nullable=False, index=True)
    result_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=_naive_utcnow, index=True)


class GrowthArtifactModel(Base):
    __tablename__ = "growth_artifacts"

    id = Column(String(100), primary_key=True, default=lambda: str(uuid.uuid4()))
    run_id = Column(String(100), nullable=False, index=True)
    project_key = Column(String(100), nullable=False, index=True)
    artifact_type = Column(String(50), nullable=False, index=True)
    format = Column(String(20), nullable=False, index=True)
    content_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_naive_utcnow, index=True)


class ConversationStateModel(Base):
    """RDB SSOT for consultation policy + question allocation."""

    __tablename__ = "conversation_state"

    project_id = Column(String(100), primary_key=True)
    policy_version = Column(String(24), nullable=False, default="v0_legacy", index=True)

    consultation_mode = Column(String(32), nullable=False, default="v0_legacy", index=True)
    profile_stage = Column(String(32), nullable=False, default="v0_legacy")

    # Runtime mode selected by user/session (자유/요건수집/도우미).
    # v1.0 정책에서 대화 흐름을 제어하는 SSOT로 사용.
    active_mode = Column(String(24), nullable=False, default="NATURAL", index=True)

    # 최신 요건/요약 유효성 제어.
    # plan_data_version은 상담 데이터가 변경될 때마다 증가,
    # summary_revision은 요약 승인 당시의 version을 기록.
    plan_data_version = Column(Integer, nullable=False, default=0)
    summary_revision = Column(Integer, nullable=False, default=0)

    question_mode = Column(String(20), nullable=False, default="legacy")
    question_required_count = Column(Integer, default=0)
    question_optional_count = Column(Integer, default=0)
    question_special_count = Column(Integer, default=0)
    question_total_count = Column(Integer, default=0)
    question_required_limit = Column(Integer, default=0)
    question_optional_limit = Column(Integer, default=0)
    question_special_limit = Column(Integer, default=0)

    active_template_id = Column(String(36), nullable=True)
    profile_slots_json = Column(JSON, nullable=False, default=dict)
    last_asked_slot = Column(String(64), nullable=True)
    plan_suspended = Column(Boolean, nullable=False, default=False)
    updated_at = Column(
        DateTime,
        default=_naive_utcnow,
        onupdate=_naive_utcnow,
    )


class ConversationStateThreadModel(Base):
    """Thread-scoped SSOT for v1.0 consultation flow state.

    Keeps 상담 정책/질문 슬롯 카운터 independent per chat room (thread),
    while ConversationStateModel remains project-level fallback for compatibility.
    """

    __tablename__ = "conversation_state_thread"

    id = Column(String(100), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(100), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, index=True)

    policy_version = Column(String(24), nullable=False, default="v0_legacy", index=True)

    consultation_mode = Column(String(32), nullable=False, default="v0_legacy", index=True)
    profile_stage = Column(String(32), nullable=False, default="v0_legacy")

    active_mode = Column(String(24), nullable=False, default="NATURAL", index=True)

    plan_data_version = Column(Integer, nullable=False, default=0)
    summary_revision = Column(Integer, nullable=False, default=0)

    question_mode = Column(String(20), nullable=False, default="legacy")
    question_required_count = Column(Integer, default=0)
    question_optional_count = Column(Integer, default=0)
    question_special_count = Column(Integer, default=0)
    question_total_count = Column(Integer, default=0)
    question_required_limit = Column(Integer, default=0)
    question_optional_limit = Column(Integer, default=0)
    question_special_limit = Column(Integer, default=0)

    active_template_id = Column(String(36), nullable=True)
    profile_slots_json = Column(JSON, nullable=False, default=dict)
    last_asked_slot = Column(String(64), nullable=True)
    plan_suspended = Column(Boolean, nullable=False, default=False)
    updated_at = Column(
        DateTime,
        default=_naive_utcnow,
        onupdate=_naive_utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "thread_id",
            name="uq_conversation_state_thread_project_thread",
        ),
    )


class ArtifactApprovalStateModel(Base):
    """Approval gate state for PDF generation by artifact."""

    __tablename__ = "artifact_approval_state"

    id = Column(String(100), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(100), nullable=False, index=True)
    thread_id = Column(String(100), nullable=False, default="", index=True)
    artifact_type = Column(String(50), nullable=False, index=True)
    requirement_version = Column(Integer, nullable=False, default=0)

    key_figures_approved = Column(Boolean, default=False, nullable=False)
    certification_path_approved = Column(Boolean, default=False, nullable=False)
    template_selected = Column(Boolean, default=False, nullable=False)
    summary_confirmed = Column(Boolean, default=False, nullable=False)
    summary_revision = Column(Integer, nullable=False, default=0)
    plan_data_version = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, default=_naive_utcnow, onupdate=_naive_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "thread_id",
            "artifact_type",
            "requirement_version",
            name="uq_artifact_approval_scope_v2",
        ),
    )


class GrowthTemplateModel(Base):
    """Template catalog with stage mapping and versioning."""

    __tablename__ = "growth_templates"

    id = Column(String(100), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False)
    artifact_type = Column(String(50), nullable=False, default="business_plan")
    stage = Column(String(32), nullable=False)
    version = Column(String(20), nullable=False)
    source_pdf = Column(Text, nullable=True)
    sections_keys_ordered = Column(JSON, nullable=True)
    template_body = Column(Text, nullable=False)
    is_active = Column(Boolean, default=False, index=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_naive_utcnow)
    updated_at = Column(
        DateTime,
        default=_naive_utcnow,
        onupdate=_naive_utcnow,
    )

    __table_args__ = (
        UniqueConstraint("artifact_type", "stage", "version", name="uq_growth_template_stage_version"),
    )


class ResearchStaticSourceType:
    """Allowed source type labels for business research collection."""

    PUBLIC_API = "public_api"
    STATIC_DB = "static_db"
    USER_INPUT = "user_input"
    LLM_SUPPORT = "llm_support"


class ResearchKnowledgeDomain:
    """고정된 연구 항목 도메인."""

    MARKET_SIZE = "market_size"
    INDUSTRY_TRENDS = "industry_trends"
    COMPETITOR_INFO = "competitor_info"
    POLICY_SUPPORT = "policy_support"


class ResearchStaticReferenceModel(Base):
    """정적 DB 테이블: 시장/산업/경쟁/정책 조회용 기본 레퍼런스."""

    __tablename__ = "research_static_reference"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    domain = Column(String(40), nullable=False, index=True)
    industry_code = Column(String(20), nullable=True, index=True)
    tag = Column(String(80), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    source_url = Column(String(600), nullable=True)
    source_text = Column(Text, nullable=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=_naive_utcnow)
    updated_at = Column(
        DateTime,
        default=_naive_utcnow,
        onupdate=_naive_utcnow,
    )

    __table_args__ = (
        UniqueConstraint(
            "domain",
            "industry_code",
            "tag",
            "title",
            name="uq_research_static_ref_key",
        ),
    )


class ProjectResearchRunModel(Base):
    """수집 실행 단위(로그/감사 목적)."""

    __tablename__ = "project_research_runs"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(100), nullable=False, index=True)
    policy_version = Column(String(24), nullable=False, default="v0_legacy", index=True)
    request_payload = Column(JSON, nullable=False, default=dict)
    started_at = Column(DateTime, default=_naive_utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(24), nullable=False, default="running", index=True)


class ProjectResearchSourceModel(Base):
    """항목별 수집 소스별 결과를 저장."""

    __tablename__ = "project_research_sources"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        String(100),
        nullable=False,
        index=True,
    )
    project_id = Column(String(100), nullable=False, index=True)
    domain = Column(String(40), nullable=False, index=True)
    source_type = Column(String(24), nullable=False, index=True)
    source_ref = Column(Text, nullable=True)
    source_version = Column(String(40), nullable=True)
    payload_json = Column(JSON, nullable=False, default=dict)
    confidence = Column(Float, nullable=False, default=0.0)
    is_success = Column(Boolean, default=False, nullable=False)
    error_message = Column(Text, nullable=True)
    collected_at = Column(DateTime, default=_naive_utcnow, index=True)


class ProjectResearchSnapshotModel(Base):
    """영역별 최신 집계 결과를 빠르게 조회/재사용하기 위한 스냅샷."""

    __tablename__ = "project_research_snapshots"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id = Column(String(100), nullable=False, index=True)
    policy_version = Column(String(24), nullable=False, default="v0_legacy", index=True)
    domain = Column(String(40), nullable=False, index=True)
    summary_json = Column(JSON, nullable=False, default=dict)
    sources_used = Column(JSON, nullable=False, default=list)
    collected_at = Column(DateTime, default=_naive_utcnow, index=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "domain",
            name="uq_project_research_snapshot_domain",
        ),
    )

# Database URL Handling
DATABASE_URL = (settings.DATABASE_URL or "").strip()
IS_PRODUCTION = settings.ENVIRONMENT.lower() == "production"
LOCAL_SQLITE_PLACEHOLDERS = {
    "postgresql://user:password@localhost:5432/buja_core",
    "postgresql+asyncpg://user:password@localhost:5432/buja_core",
}

def _resolve_database_schema() -> str:
    schema = (settings.DATABASE_SCHEMA or "public").strip()
    if not schema:
        return "public"
    if schema.lower() == "public":
        return "public"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError(
            "DATABASE_SCHEMA must be a valid PostgreSQL identifier"
        )
    return schema


def _build_default_sqlite_url() -> str:
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db_path = os.path.join(base_dir, "data", "buja.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


def _has_asyncpg() -> bool:
    return importlib.util.find_spec("asyncpg") is not None


def _resolve_database_url() -> str:
    strict_mode = bool(settings.STRICT_DB_MODE) or IS_PRODUCTION
    allow_without_postgres = bool(settings.STARTUP_WITHOUT_POSTGRES)
    unresolved = (not DATABASE_URL) or (DATABASE_URL in LOCAL_SQLITE_PLACEHOLDERS)

    if unresolved:
        if strict_mode and not allow_without_postgres:
            raise RuntimeError(
                "DATABASE_URL is required in production/strict mode. SQLite fallback is blocked."
            )
        logger.warning(
            "DATABASE_URL unresolved; falling back to sqlite",
            strict_mode=strict_mode,
            allow_without_postgres=allow_without_postgres,
        )
        return _build_default_sqlite_url()

    selected_url = DATABASE_URL

    if selected_url.startswith("postgresql://"):
        if strict_mode:
            raise RuntimeError(
                "DATABASE_URL must use postgresql+asyncpg:// in production/strict mode."
            )
        if _has_asyncpg():
            selected_url = selected_url.replace("postgresql://", "postgresql+asyncpg://", 1)
            logger.warning("DATABASE_URL upgraded to postgresql+asyncpg://")
        else:
            raise RuntimeError(
                "DATABASE_URL uses postgresql:// but asyncpg is not available."
            )

    if selected_url.startswith("postgresql+asyncpg://") and not _has_asyncpg():
        raise RuntimeError("DATABASE_URL requires asyncpg, but asyncpg is not installed.")

    if IS_PRODUCTION and ("localhost" in selected_url.lower() or "127.0.0.1" in selected_url):
        raise RuntimeError("DATABASE_URL must not use localhost/127.0.0.1 in production.")

    if IS_PRODUCTION and selected_url.startswith("sqlite"):
        raise RuntimeError("SQLite is not allowed when ENVIRONMENT=production.")

    return selected_url


DATABASE_SCHEMA = _resolve_database_schema()
DATABASE_URL = _resolve_database_url()
DATABASE_CONNECT_ARGS = (
    {"check_same_thread": False, "timeout": 30} if "sqlite" in DATABASE_URL else {}
)
if "postgresql" in DATABASE_URL and DATABASE_SCHEMA != "public":
    DATABASE_CONNECT_ARGS["server_settings"] = {"search_path": f'"{DATABASE_SCHEMA}", public'}

# [UTF-8] Ensure all JSON serialization in DB uses ensure_ascii=False
_init_db_lock = asyncio.Lock()
_initialized_db = False
_is_pytest = (
    "PYTEST_CURRENT_TEST" in os.environ
    or "pytest" in sys.modules
)

_engine_kwargs = {
    "url": DATABASE_URL,
    "echo": False,
    "json_serializer": (lambda obj: json.dumps(obj, ensure_ascii=False)),
    "connect_args": DATABASE_CONNECT_ARGS,
    "pool_pre_ping": True,
}
if _is_pytest:
    _engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(**_engine_kwargs)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

_thread_soft_delete_columns_ready = False
_thread_soft_delete_columns_lock = asyncio.Lock()
_artifact_approval_columns_ready = False
_artifact_approval_columns_lock = asyncio.Lock()
_conversation_state_slot_columns_ready = False
_conversation_state_slot_columns_lock = asyncio.Lock()


def _thread_columns_needs_soft_delete_sync(sync_conn) -> bool:
    """Return True when threads.is_deleted / threads.deleted_at columns are missing."""
    try:
        if "sqlite" in DATABASE_URL:
            rows = sync_conn.execute(text("PRAGMA table_info(threads)")).fetchall()
            columns = {row[1] for row in rows}
        else:
            if "postgresql" not in DATABASE_URL:
                return False
            rows = sync_conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = :schema_name AND table_name = 'threads'
                    """
                ),
                {"schema_name": DATABASE_SCHEMA},
            ).fetchall()
            columns = {row[0] for row in rows}

        return "is_deleted" not in columns or "deleted_at" not in columns
    except Exception:
        # conservative: if introspection fails, assume no-op here and let caller recover via migration path.
        return False


async def ensure_threads_soft_delete_columns() -> None:
    """Ensure legacy DB has is_deleted/deleted_at columns even if init_db() was not executed."""
    global _thread_soft_delete_columns_ready

    if _thread_soft_delete_columns_ready:
        return

    async with _thread_soft_delete_columns_lock:
        if _thread_soft_delete_columns_ready:
            return

        async def _run_sync(conn):
            from app.core.database import _ensure_threads_soft_delete_columns_sync
            _ensure_threads_soft_delete_columns_sync(conn)

        # Run on dedicated connection to avoid nested transaction issues.
        async with engine.begin() as conn:
            await conn.run_sync(_run_sync)

        _thread_soft_delete_columns_ready = True


async def ensure_artifact_approval_columns() -> None:
    """Ensure legacy DB has artifact_approval_state.summary_revision/plan_data_version columns."""
    global _artifact_approval_columns_ready

    if _artifact_approval_columns_ready:
        return

    async with _artifact_approval_columns_lock:
        if _artifact_approval_columns_ready:
            return

        async def _run_sync(conn):
            from app.core.database import _ensure_artifact_approval_columns_sync
            _ensure_artifact_approval_columns_sync(conn)

        async with engine.begin() as conn:
            await conn.run_sync(_run_sync)

        _artifact_approval_columns_ready = True


async def ensure_conversation_state_slot_columns() -> None:
    """Ensure conversation_state(_thread) has slot-memory columns for v1.0."""
    global _conversation_state_slot_columns_ready

    if _conversation_state_slot_columns_ready:
        return

    async with _conversation_state_slot_columns_lock:
        if _conversation_state_slot_columns_ready:
            return

        async def _run_sync(conn):
            from app.core.database import _ensure_conversation_state_slot_columns_sync
            _ensure_conversation_state_slot_columns_sync(conn)

        async with engine.begin() as conn:
            await conn.run_sync(_run_sync)

        _conversation_state_slot_columns_ready = True


def _ensure_threads_soft_delete_columns_sync(sync_conn):
    """Ensure thread scope columns exist even on legacy schemas."""
    if "sqlite" in DATABASE_URL:
        pragma_rows = sync_conn.execute(text("PRAGMA table_info(threads)")).fetchall()
        existing = {row[1] for row in pragma_rows}
        if "owner_user_id" not in existing:
            sync_conn.execute(text("ALTER TABLE threads ADD COLUMN owner_user_id VARCHAR(50)"))
        if "is_deleted" not in existing:
            sync_conn.execute(text("ALTER TABLE threads ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT 0"))
        if "deleted_at" not in existing:
            sync_conn.execute(text("ALTER TABLE threads ADD COLUMN deleted_at DATETIME"))
        return

    if "postgresql" not in DATABASE_URL:
        return

    rows = sync_conn.execute(text(
        "SELECT table_schema FROM information_schema.tables WHERE table_name = 'threads'"
    )).fetchall()
    schemas = [row[0] for row in rows]
    if not schemas:
        return

    preferred_schema = DATABASE_SCHEMA if DATABASE_SCHEMA in schemas else schemas[0]
    existing_cols = sync_conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema_name
              AND table_name = 'threads'
            """
        ),
        {"schema_name": preferred_schema},
    ).fetchall()
    existing = {row[0] for row in existing_cols}

    table_name = f'"{preferred_schema}"."threads"'
    if "owner_user_id" not in existing:
        sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN owner_user_id VARCHAR(50);"))
    if "is_deleted" not in existing:
        sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN is_deleted BOOLEAN NOT NULL DEFAULT FALSE;"))
    if "deleted_at" not in existing:
        sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN deleted_at TIMESTAMP;"))


def _ensure_artifact_approval_columns_sync(sync_conn):
    """Ensure artifact_approval_state extension columns exist for v1.0 approval contract."""
    if "sqlite" in DATABASE_URL:
        pragma_rows = sync_conn.execute(text("PRAGMA table_info(artifact_approval_state)")).fetchall()
        existing = {row[1] for row in pragma_rows}
        if "thread_id" not in existing:
            sync_conn.execute(
                text("ALTER TABLE artifact_approval_state ADD COLUMN thread_id VARCHAR(100) NOT NULL DEFAULT ''")
            )
        if "requirement_version" not in existing:
            sync_conn.execute(
                text("ALTER TABLE artifact_approval_state ADD COLUMN requirement_version INTEGER NOT NULL DEFAULT 0")
            )
        if "summary_revision" not in existing:
            sync_conn.execute(
                text("ALTER TABLE artifact_approval_state ADD COLUMN summary_revision INTEGER NOT NULL DEFAULT 0")
            )
        if "plan_data_version" not in existing:
            sync_conn.execute(
                text("ALTER TABLE artifact_approval_state ADD COLUMN plan_data_version INTEGER NOT NULL DEFAULT 0")
            )
        return

    if "postgresql" not in DATABASE_URL:
        return

    rows = sync_conn.execute(
        text(
            """
            SELECT table_schema
            FROM information_schema.tables
            WHERE table_name = 'artifact_approval_state'
            """
        )
    ).fetchall()
    schemas = [row[0] for row in rows]
    if not schemas:
        return

    preferred_schema = DATABASE_SCHEMA if DATABASE_SCHEMA in schemas else schemas[0]
    existing_cols = sync_conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = :schema_name
              AND table_name = 'artifact_approval_state'
            """
        ),
        {"schema_name": preferred_schema},
    ).fetchall()
    existing = {row[0] for row in existing_cols}

    table_name = f'"{preferred_schema}"."artifact_approval_state"'
    if "thread_id" not in existing:
        sync_conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN thread_id VARCHAR(100) NOT NULL DEFAULT '';")
        )
    if "requirement_version" not in existing:
        sync_conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN requirement_version INTEGER NOT NULL DEFAULT 0;")
        )
    if "summary_revision" not in existing:
        sync_conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN summary_revision INTEGER NOT NULL DEFAULT 0;")
        )
    if "plan_data_version" not in existing:
        sync_conn.execute(
            text(f"ALTER TABLE {table_name} ADD COLUMN plan_data_version INTEGER NOT NULL DEFAULT 0;")
        )

    # 기존 unique constraint(project_id, artifact_type)가 있으면 v2 scope로 교체
    old_constraint = sync_conn.execute(
        text(
            """
            SELECT conname
            FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = :schema_name
              AND c.conrelid = to_regclass(:table_name)
              AND c.contype = 'u'
              AND c.conname = 'uq_artifact_approval_project_artifact'
            """
        ),
        {
            "schema_name": preferred_schema,
            "table_name": f'{preferred_schema}.artifact_approval_state',
        },
    ).fetchone()
    if old_constraint:
        sync_conn.execute(
            text(f'ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS uq_artifact_approval_project_artifact;')
        )

    new_constraint = sync_conn.execute(
        text(
            """
            SELECT conname
            FROM pg_constraint c
            JOIN pg_namespace n ON n.oid = c.connamespace
            WHERE n.nspname = :schema_name
              AND c.conrelid = to_regclass(:table_name)
              AND c.contype = 'u'
              AND c.conname = 'uq_artifact_approval_scope_v2'
            """
        ),
        {
            "schema_name": preferred_schema,
            "table_name": f'{preferred_schema}.artifact_approval_state',
        },
    ).fetchone()
    if not new_constraint:
        sync_conn.execute(
            text(
                f"""
                ALTER TABLE {table_name}
                ADD CONSTRAINT uq_artifact_approval_scope_v2
                UNIQUE (project_id, thread_id, artifact_type, requirement_version);
                """
            )
        )


def _ensure_conversation_state_slot_columns_sync(sync_conn):
    """Ensure slot memory columns exist in conversation_state and conversation_state_thread."""
    slot_cols_sqlite = (
        ("profile_slots_json", "TEXT"),
        ("last_asked_slot", "VARCHAR(64)"),
        ("plan_suspended", "BOOLEAN NOT NULL DEFAULT 0"),
    )
    slot_cols_pg = {
        "profile_slots_json": "JSONB NOT NULL DEFAULT '{}'::jsonb",
        "last_asked_slot": "VARCHAR(64)",
        "plan_suspended": "BOOLEAN NOT NULL DEFAULT FALSE",
    }

    def _ensure_sqlite_table(table_name: str):
        pragma_rows = sync_conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
        existing = {row[1] for row in pragma_rows}
        for col_name, col_type in slot_cols_sqlite:
            if col_name not in existing:
                sync_conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}"))

    if "sqlite" in DATABASE_URL:
        _ensure_sqlite_table("conversation_state")
        _ensure_sqlite_table("conversation_state_thread")
        return

    if "postgresql" not in DATABASE_URL:
        return

    rows = sync_conn.execute(
        text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name IN ('conversation_state', 'conversation_state_thread')
            """
        )
    ).fetchall()
    table_schema_map = {row[1]: row[0] for row in rows}
    if not table_schema_map:
        return

    for table_name in ("conversation_state", "conversation_state_thread"):
        schema = table_schema_map.get(table_name)
        if not schema:
            continue
        existing_cols = sync_conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema_name
                  AND table_name = :table_name
                """
            ),
            {"schema_name": schema, "table_name": table_name},
        ).fetchall()
        existing = {row[0] for row in existing_cols}
        full_table = f'"{schema}"."{table_name}"'
        for col_name, col_type in slot_cols_pg.items():
            if col_name not in existing:
                sync_conn.execute(
                    text(f"ALTER TABLE {full_table} ADD COLUMN {col_name} {col_type};")
                )

# [UTF-8] Force SQLite to use UTF-8 encoding
from sqlalchemy import event
@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in DATABASE_URL:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA encoding = 'UTF-8'")
        cursor.close()

async def init_db():
    global _initialized_db
    async with _init_db_lock:
        if _initialized_db:
            return
        async with engine.begin() as conn:
            if "sqlite" not in DATABASE_URL and DATABASE_SCHEMA != "public":
                def _prepare_schema(sync_conn):
                    sync_conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{DATABASE_SCHEMA}"'))
                    sync_conn.execute(
                        text(f'SET search_path TO "{DATABASE_SCHEMA}", public')
                    )
                await conn.run_sync(_prepare_schema)

            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_ensure_threads_soft_delete_columns_sync)
            await conn.run_sync(_ensure_artifact_approval_columns_sync)
            await conn.run_sync(_ensure_conversation_state_slot_columns_sync)
        _initialized_db = True

    # [Neo4j] Create indexes for optimized searching
    from app.core.neo4j_client import neo4j_client
    try:
        await neo4j_client.create_indexes()
    except Exception as e:
        logger.warning("Failed to create Neo4j indexes during init", error=str(e))

def _normalize_project_id(project_id: str) -> Optional[uuid.UUID]:
    """
    Task 1.4: Normalize project_id to ensure case-insensitive consistent UUID generation
    Per CONVERSATION_CONSISTENCY.md
    """
    if not project_id or project_id == "system-master":
        return None
    
    try:
        if isinstance(project_id, uuid.UUID):
            return project_id
        # If already a valid UUID, return it
        return uuid.UUID(project_id)
    except (ValueError, AttributeError):
        # Normalize: lowercase + strip, then generate deterministic UUID
        normalized = str(project_id).lower().strip()
        return uuid.uuid5(uuid.NAMESPACE_DNS, normalized)


async def save_message_to_rdb(
    role: str, 
    content: str, 
    project_id: str = None, 
    thread_id: str = None, 
    metadata: dict = None,
    owner_user_id: str | None = None,
) -> Tuple[uuid.UUID, str]:
    """
    Save message to RDB. Single Source of Truth.
    Task 1.5: Returns (message_id, thread_id) per CONVERSATION_CONSISTENCY.md
    Auto-generates thread_id if None.
    """
    if thread_id in ["null", "undefined", ""]:
        thread_id = None

    # 보수: legacy DB에서 is_deleted 컬럼이 없을 수 있어 시작 시점에 정합성 보정
    await ensure_threads_soft_delete_columns()
    
    # Task 1.5: Auto-generate thread_id if not provided
    if thread_id is None:
        thread_id = f"thread-{uuid.uuid4()}"
        
    derived_owner_user_id = owner_user_id
    if not derived_owner_user_id and isinstance(metadata, dict):
        candidate_owner = metadata.get("user_id")
        if candidate_owner:
            derived_owner_user_id = str(candidate_owner)

    async with AsyncSessionLocal() as session:
        msg_id = uuid.uuid4()
        
        # Task 1.4: Use normalized project_id
        p_id = _normalize_project_id(project_id)

        # 보안/정합성: 메시지의 thread_id가 ThreadModel에 없는 값이면
        # fallback로 해당 thread_id에 대해 Thread 레코드를 보정 생성한다.
        thread_row = (await session.execute(
            select(ThreadModel).where(
                ThreadModel.id == thread_id,
                ThreadModel.project_id == p_id,
            )
        )).scalar_one_or_none()

        if thread_row is not None and thread_row.is_deleted:
            # 삭제된 방으로 메시지 쓰기 방지: 새 방으로 전환
            thread_id = f"thread-{uuid.uuid4()}"
            thread_row = None

        if thread_row is None:
            thread_title = "기본 상담방"
            if isinstance(metadata, dict):
                thread_title = metadata.get("thread_title") or metadata.get("title") or thread_title
                if thread_title == "기본 상담방":
                    thread_title = "새 상담방"
            session.add(
                ThreadModel(
                    id=thread_id,
                    project_id=p_id,
                    owner_user_id=derived_owner_user_id,
                    title=str(thread_title)[:255],
                    is_deleted=False,
                )
            )
        else:
            if not thread_row.owner_user_id and derived_owner_user_id:
                thread_row.owner_user_id = derived_owner_user_id
            # 최신 메시지 반영을 위해 스레드 갱신 시각을 즉시 업데이트
            thread_row.updated_at = _naive_utcnow()
        
        new_msg = MessageModel(
            message_id=msg_id,
            project_id=p_id,
            thread_id=thread_id,
            sender_role=role,
            content=content,
            metadata_json=metadata
        )
        session.add(new_msg)
        await session.commit()
        return (msg_id, thread_id)


async def get_or_create_default_thread(
    project_id: str,
    title: str = "기본 상담방",
    owner_user_id: str | None = None,
) -> Optional[str]:
    """
    Resolve existing thread for project; if none exists create a default thread.
    """
    await ensure_threads_soft_delete_columns()
    p_id = _normalize_project_id(project_id)
    async with AsyncSessionLocal() as session:
        if project_id == "system-master":
            stmt = select(ThreadModel).where(
                (ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master"),
                ThreadModel.is_deleted.is_(False),
            ).order_by(ThreadModel.updated_at.desc())
        else:
            stmt = select(ThreadModel).where(
                ThreadModel.project_id == p_id,
                ThreadModel.is_deleted.is_(False),
            ).order_by(ThreadModel.updated_at.desc())
        if owner_user_id:
            stmt = stmt.where(ThreadModel.owner_user_id == owner_user_id)
        row = (await session.execute(stmt)).scalars().first()
        if row:
            return row.id

        thread_id = f"thread-{uuid.uuid4()}"
        session.add(
            ThreadModel(
                id=thread_id,
                project_id=p_id,
                owner_user_id=owner_user_id,
                title=title,
                is_deleted=False,
            )
        )
        await session.commit()
        return thread_id


async def resolve_thread_id_for_project(
    project_id: str,
    requested_thread_id: Optional[str] = None,
    create_if_missing: bool = True,
    owner_user_id: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve a valid thread id for chat. Priority: requested -> latest thread -> latest message group -> create default.
    """
    await ensure_threads_soft_delete_columns()
    requested = requested_thread_id
    if requested in ["null", "undefined", ""]:
        requested = None

    p_id = _normalize_project_id(project_id)
    async with AsyncSessionLocal() as session:
        if requested:
            if project_id == "system-master":
                req_stmt = select(ThreadModel).where(
                    ThreadModel.id == requested,
                    (ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master"),
                    ThreadModel.is_deleted.is_(False),
                )
            else:
                req_stmt = select(ThreadModel).where(
                    ThreadModel.id == requested,
                    ThreadModel.project_id == p_id,
                    ThreadModel.is_deleted.is_(False),
                )
            if owner_user_id:
                req_stmt = req_stmt.where(ThreadModel.owner_user_id == owner_user_id)
            req_row = (await session.execute(req_stmt)).scalar_one_or_none()
            if req_row:
                return req_row.id

            # [v1.0 Fix] Fallback: ThreadModel이 비어있더라도 메시지 기반으로 스레드 존재를 인정
            # 기존 운영 데이터(legacy)에서 thread_id만 존재하고 ThreadModel이 누락된 케이스를 방지.
            from sqlalchemy import exists
            if project_id == "system-master":
                msg_exists_stmt = select(
                    exists().where(
                        MessageModel.project_id.in_([None, p_id]),
                        MessageModel.thread_id == requested,
                    )
                )
            else:
                msg_exists_stmt = select(
                    exists().where(
                        MessageModel.project_id == p_id,
                        MessageModel.thread_id == requested,
                    )
                )
            msg_exists = (await session.execute(msg_exists_stmt)).scalar_one_or_none()
            if msg_exists:
                return requested

        if project_id == "system-master":
            latest_stmt = select(ThreadModel).where(
                (ThreadModel.project_id == None) | (ThreadModel.project_id == "system-master"),
                ThreadModel.is_deleted.is_(False),
            ).order_by(ThreadModel.updated_at.desc())
        else:
            latest_stmt = select(ThreadModel).where(
                ThreadModel.project_id == p_id,
                ThreadModel.is_deleted.is_(False),
            ).order_by(ThreadModel.updated_at.desc())
        if owner_user_id:
            latest_stmt = latest_stmt.where(ThreadModel.owner_user_id == owner_user_id)
        latest_row = (await session.execute(latest_stmt)).scalars().first()
        if latest_row:
            return latest_row.id

        from sqlalchemy import func
        if project_id == "system-master":
            msg_stmt = (
                select(MessageModel.thread_id, func.max(MessageModel.timestamp).label("last_update"))
                .where((MessageModel.project_id == None) | (MessageModel.project_id == "system-master"))
                .group_by(MessageModel.thread_id)
                .order_by(func.max(MessageModel.timestamp).desc())
                .limit(1)
            )
        else:
            msg_stmt = (
                select(MessageModel.thread_id, func.max(MessageModel.timestamp).label("last_update"))
                .where(MessageModel.project_id == p_id)
                .group_by(MessageModel.thread_id)
                .order_by(func.max(MessageModel.timestamp).desc())
                .limit(1)
            )
        msg_row = (await session.execute(msg_stmt)).first()
        if msg_row and msg_row[0]:
            return msg_row[0]

    if create_if_missing:
        return await get_or_create_default_thread(project_id, owner_user_id=owner_user_id)
    return None


async def get_messages_from_rdb(project_id: str = None, thread_id: str = None, limit: int = 50):
    if thread_id in ["null", "undefined", ""]:
        thread_id = None
        
    from sqlalchemy import select, or_
    async with AsyncSessionLocal() as session:
        query = select(MessageModel)
        
        # [Task 1.4/1.5 Update] system-master는 NULL로 저장되므로 명시적으로 필터링
        if project_id == "system-master":
            query = query.filter(or_(MessageModel.project_id == None, MessageModel.project_id == "system-master"))
        elif project_id:
            p_id = _normalize_project_id(project_id)
            query = query.filter(MessageModel.project_id == p_id)
            
        if thread_id:
            query = query.filter(MessageModel.thread_id == thread_id)
        
        query = query.order_by(MessageModel.timestamp.asc()).limit(limit)
        result = await session.execute(query)
        return result.scalars().all()

# ===== [v3.2] Shadow Mining - Draft Storage =====

async def save_draft_to_rdb(draft) -> str:
    """
    Draft를 PostgreSQL에 저장 (Using declarative model)
    Returns: draft_id
    """
    async with AsyncSessionLocal() as session:
        new_draft = DraftModel(
            id=draft.id,
            session_id=draft.session_id,
            user_id=draft.user_id,
            project_id=draft.project_id,
            status=draft.status,
            category=draft.category,
            content=draft.content,
            source=draft.source,
            timestamp=draft.timestamp,
            ttl_days=draft.ttl_days
        )
        # Using merge to handle potential updates (upsert behavior)
        await session.merge(new_draft)
        await session.commit()
        return draft.id

async def get_drafts_from_rdb(session_id: str = None, status: str = "UNVERIFIED") -> List:
    """
    Draft 조회 (Using declarative model)
    """
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        query = select(DraftModel)
        
        if session_id:
            query = query.filter(DraftModel.session_id == session_id)
        if status:
            query = query.filter(DraftModel.status == status)
        
        query = query.order_by(DraftModel.timestamp.desc())
        result = await session.execute(query)
        return result.scalars().all()

async def delete_expired_drafts(days: int = 7):
    """
    만료된 Draft 삭제 (TTL 기반) (Using declarative model)
    """
    from sqlalchemy import delete
    async with AsyncSessionLocal() as session:
        expired_time = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = delete(DraftModel).where(
            DraftModel.status == 'UNVERIFIED',
            DraftModel.timestamp < expired_time
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount


def _normalize_project_key(project_id: str) -> str:
    if not project_id:
        return "system-master"
    return str(project_id).strip().lower()


async def save_growth_run(project_id: str, result_json: dict, artifacts: dict) -> str:
    run_id = str(uuid.uuid4())
    project_key = _normalize_project_key(project_id)
    async with AsyncSessionLocal() as session:
        run = GrowthRunModel(
            id=run_id,
            project_key=project_key,
            result_json=result_json,
        )
        session.add(run)
        for artifact_type, format_map in artifacts.items():
            for fmt, content in format_map.items():
                if isinstance(content, str):
                    session.add(
                        GrowthArtifactModel(
                            run_id=run_id,
                            project_key=project_key,
                            artifact_type=artifact_type,
                            format=fmt,
                            content_text=content,
                        )
                    )
        await session.commit()
    return run_id


async def get_latest_growth_run(project_id: str) -> Optional[dict]:
    from sqlalchemy import select

    project_key = _normalize_project_key(project_id)
    async with AsyncSessionLocal() as session:
        stmt = (
            select(GrowthRunModel)
            .where(GrowthRunModel.project_key == project_key)
            .order_by(GrowthRunModel.created_at.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        return row.result_json if row else None


async def get_latest_growth_artifact(project_id: str, artifact_type: str, fmt: str) -> Optional[str]:
    from sqlalchemy import select

    project_key = _normalize_project_key(project_id)
    async with AsyncSessionLocal() as session:
        stmt = (
            select(GrowthArtifactModel)
            .where(
                GrowthArtifactModel.project_key == project_key,
                GrowthArtifactModel.artifact_type == artifact_type,
                GrowthArtifactModel.format == fmt,
            )
            .order_by(GrowthArtifactModel.created_at.desc())
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        return row.content_text if row else None
