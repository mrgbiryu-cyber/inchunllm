"""
Configuration settings for AI BizPlan Backend
Loads environment variables and provides typed configuration.
"""
import os
from typing import Optional
from dotenv import load_dotenv  # 👈 [추가] 강제 로딩 도구

# 1. 👇 Pydantic이 읽기 전에, 우리가 먼저 강제로 읽어버립니다.
# (현재 폴더의 .env를 시스템 환경변수로 로드함)
load_dotenv()

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Application
    APP_NAME: str = "AIBizPlan"
    APP_TAGLINE: str = "AI BizPlan / cowork AI 기반 사업계획서(인증/지원 연계)"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8002
    
    # Database
    DATABASE_URL: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_URL", "POSTGRESQL_URL"),
    )
    DATABASE_SCHEMA: str = "buja_app"
    REDIS_URL: Optional[str] = None
    STARTUP_WITHOUT_REDIS: bool = False
    STARTUP_WITHOUT_POSTGRES: bool = False
    STRICT_DB_MODE: bool = False
    NEO4J_URI: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("NEO4J_URI", "NEO4J_URL"),
    )
    NEO4J_USER: Optional[str] = None
    NEO4J_PASSWORD: Optional[str] = None
    
    # Vector Database
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_ENVIRONMENT: str = "us-west1-gcp"
    PINECONE_INDEX_NAME: str = "buja-knowledge"
    
    # LLM Providers
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    BUSINESS_PLAN_DRAFT_MODEL: str = "google/gemini-3-flash-preview"
    BUSINESS_PLAN_POLISH_MODEL: str = "openai/gpt-5-mini"
    BUSINESS_PLAN_FORMAT_MODEL: str = "openai/gpt-5.2"
    BUSINESS_PLAN_FIELD_EXTRACTION_MODEL: str = "openai/gpt-5-mini"
    BUSINESS_PLAN_DRAFT_SYSTEM_PROMPT: Optional[str] = None
    BUSINESS_PLAN_POLISH_SYSTEM_PROMPT: Optional[str] = None
    BUSINESS_PLAN_FORMAT_SYSTEM_PROMPT: Optional[str] = None
    BUSINESS_PLAN_FIELD_EXTRACTION_SYSTEM_PROMPT: Optional[str] = None
    
    # Search
    TAVILY_API_KEY: Optional[str] = None
    
    # Observability
    LANGFUSE_PUBLIC_KEY: Optional[str] = None
    LANGFUSE_SECRET_KEY: Optional[str] = None
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
    
    # Authentication & Security
    # 2. 👇 이제 환경변수에서 값을 가져옵니다. (없으면 에러)
    JWT_SECRET_KEY: str = Field(..., description="Secret key for JWT signing")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    
    # Ed25519 Job Signing Keys
    JOB_SIGNING_PRIVATE_KEY: str = Field(..., description="Ed25519 private key in PEM format")
    JOB_SIGNING_PUBLIC_KEY: str = Field(..., description="Ed25519 public key in PEM format")
    
    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_WEBHOOK_URL: Optional[str] = None
    
    # Rate Limiting & Quotas
    DEFAULT_MONTHLY_QUOTA_USD: float = 100.0
    RATE_LIMIT_PER_TENANT_PER_MINUTE: int = 100
    RATE_LIMIT_PER_USER_PER_SECOND: int = 10
    
    # Job Queue Configuration
    MAX_QUEUED_JOBS_PER_TENANT: int = 50
    JOB_DEFAULT_TIMEOUT_SEC: int = 600
    JOB_MAX_TIMEOUT_SEC: int = 3600
    JOB_MAX_RETRIES: int = 2
    JOB_DLQ_TTL_SEC: int = 1209600
    
    # Worker Management
    WORKER_HEARTBEAT_TIMEOUT_SEC: int = 120
    WORKER_MAX_REASSIGN_COUNT: int = 2
    
    # File System Safety
    MAX_FILE_SIZE_BYTES: int = 1048576  # 1 MB
    MAX_TOTAL_JOB_SIZE_BYTES: int = 10485760  # 10 MB
    
    # CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000,http://100.77.67.1:3000"
    CORS_ALLOW_CREDENTIALS: bool = True

    # Health check timeout (milliseconds)
    HEALTH_REDIS_TIMEOUT_MS: int = 300
    HEALTH_POSTGRES_TIMEOUT_MS: int = 500
    HEALTH_NEO4J_TIMEOUT_MS: int = 700
    
    # [NEW] Cost Safety Guard Configuration
    LLM_HIGH_TIER_MODEL: str = "google/gemini-2.0-flash-001"
    LLM_LOW_TIER_MODEL: str = "gpt-4o-mini"
    DAILY_BUDGET_USD: float = 5.0
    COST_FILTER_MIN_CHARS: int = 10
    BATCH_INTERVAL_SEC: int = 5  # [v5.0 DEBUG] Reduced from 30 for faster testing
    
    # [PHASE3_MVP] Model Strategy (Deterministic Baseline)
    # Primary/Secondary 모델은 "한 곳(config)에서만" 관리합니다.
    PRIMARY_MODEL: str = "google/gemini-2.0-flash-001"  # ✅ DeepSeek V3보다 가성비 좋은 최신 모델
    FALLBACK_MODEL: str = "gpt-4o-mini"            # 예시: OpenAI/OpenRouter용

    # Secondary 모델 호출 제한 (태스크당 1회만 허용)
    ALLOW_SECONDARY_MODEL: bool = True
    MAX_SECONDARY_CALLS_PER_TASK: int = 1

    # [PHASE3_MVP] Degraded Mode flags (자동제어 아님: 실패 시에도 계속 진행하기 위한 규칙 스위치)
    FORCE_DEGRADED_MODE: bool = False
    ALLOW_MISSING_RETRIEVAL: bool = True

    # [PHASE3_MVP] Web Search behavior (Tavily Optional)
    WEB_SEARCH_PROVIDER: str = "tavily"
    WEB_SEARCH_TIMEOUT_SECONDS: int = 12

    
    
    # Monitoring
    SENTRY_DSN: Optional[str] = None
    SENTRY_ENVIRONMENT: str = "development"
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0
    
    # Pydantic 설정 (보조 수단으로 남겨둠)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Global settings instance
settings = Settings()
