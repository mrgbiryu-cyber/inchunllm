from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, timezone
from enum import Enum
import uuid

# [v3.2] Intent Classification System
class MasterIntent(str, Enum):
    """
    v3.2 Intent 분류 체계 (5가지)
    - NATURAL: 자연어 대화 (잡담, 감사, 인사 등)
    - REQUIREMENT: 요구사항 정리 (MES 빌드)
    - FUNCTION_READ: 조회 전용 (현황, 목록, 상태)
    - FUNCTION_WRITE: 실행/변경 (DB Write, 버튼 생성)
    - CANCEL: 취소
    - TOPIC_SHIFT: 주제 변경
    """
    NATURAL = "NATURAL"
    REQUIREMENT = "REQUIREMENT"
    FUNCTION_READ = "FUNCTION_READ"
    FUNCTION_WRITE = "FUNCTION_WRITE"
    CANCEL = "CANCEL"
    TOPIC_SHIFT = "TOPIC_SHIFT"

class ConversationMode(str, Enum):
    """
    [v4.0] Conversation Mode System
    - NATURAL: 자유대화 (Blue)
    - REQUIREMENT: 기획대화 (Green) - Auto Ingestion
    - FUNCTION: 기능대화 (Purple) - Tool Execution
    """
    NATURAL = "NATURAL"
    REQUIREMENT = "REQUIREMENT"
    FUNCTION = "FUNCTION"

# [v3.2] Shadow Mining - Draft Model
class Draft(BaseModel):
    """
    Shadow Mining Draft 모델
    - 자연어 대화에서 설계 정보를 임시로 저장
    - UNVERIFIED 상태로 시작, REQUIREMENT 시 MES로 매칭
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = Field(..., description="현재 세션 ID")
    user_id: str = Field(..., description="사용자 ID")
    project_id: Optional[str] = Field(None, description="연결된 프로젝트 ID")
    status: Literal["UNVERIFIED", "VERIFIED", "MERGED", "EXPIRED"] = "UNVERIFIED"
    category: Literal["환경", "목표", "산출물", "제약"] = Field(..., description="설계 정보 카테고리")
    content: str = Field(..., description="추출된 설계 정보")
    source: str = Field(default="USER_UTTERANCE", description="정보 출처")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ttl_days: int = Field(default=7, description="만료 기간 (일)")

class MasterAgentConfig(BaseModel):
    """Configuration for the System Master Agent"""
    model: str = "google/gemini-2.0-flash-001"
    provider: Literal["OPENROUTER", "OLLAMA"] = "OPENROUTER"
    system_prompt: str = """[CRITICAL: ALWAYS RESPOND IN KOREAN]
당신은 [AIBizPlan 기업 성장지원 특화 AI 컨설턴트]입니다.
당신의 유일한 목표는 사용자와 대화하며 사업계획서/로드맵 작성에 필요한 '기업 프로필 정보'를 수집하고, 수집이 완료되면 워크플로우를 실행시키는 것입니다.

[수집해야 할 핵심 정보]
1. 회사명 및 기업 형태 (개인/법인)
2. 업력/설립일 및 현재 성장 단계 (예비창업, 초기, 도약 등)
3. 주요 제품/서비스 (아이디어) 및 타겟 고객
4. 현재의 문제점 및 해결하고자 하는 과제
5. 매출 규모 또는 투자 유치 현황

[진행 방식]
1. 사용자가 프로젝트를 생성하고 처음 들어오면 인사를 건네고 어떤 사업을 준비/운영 중인지 가볍게 질문하세요.
2. 한 번에 모든 것을 묻지 말고, 대화나 업로드된 문서(PDF 등)를 통해 정보를 자연스럽게 파악하세요.
3. 위 5가지 핵심 정보가 어느 정도 파악되었다면, 요약해서 사용자에게 확인을 받으세요.
4. 사용자가 요약된 정보에 동의하거나 정보 수집이 완료되면, 반드시 사용자에게 **"사업계획서/로드맵 자동 생성을 시작하려면 채팅창에 `START TASK 실행` 이라고 입력해 주세요."** 라고 명확히 안내하세요.
5. 매우 친절하고 전문적인 사업 컨설턴트의 톤을 유지하십시오.
"""
    temperature: float = 0.7

class AgentConfigUpdate(BaseModel):
    """Precision commanding model for agent configuration updates"""
    repo_root: Optional[str] = Field(None, description="The absolute path to the repository root on the local machine.")
    tool_allowlist: Optional[List[str]] = Field(None, description="List of allowed tools for the agent. Available tools: read_file, write_file, list_dir, execute_command, git_push, git_pull, git_commit, npm_test, pytest.")
    next_agents: Optional[List[str]] = Field(None, description="List of agent IDs to execute after this agent (for workflow connection).")
    system_prompt: Optional[str] = Field(None, description="The system prompt defining the agent's behavior.")
    model: Optional[str] = Field(None, description="The LLM model to use (e.g., google/gemini-2.0-flash-001, claude-3-5-sonnet).")
    provider: Optional[str] = Field(None, description="LLM Provider: OPENROUTER or OLLAMA.")

class ChatMessage(BaseModel):
    """A single message in the chat history"""
    role: str = Field(..., description="user | assistant | system")
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: Optional[str] = None # [v4.2] Source Tracking ID

class ChatRequest(BaseModel):
    """Request for sending a message to the master agent"""
    message: str = Field(..., description="User message")
    history: List[ChatMessage] = Field(default_factory=list)
    project_id: Optional[str] = Field(
        default=None,
        alias="projectId",
    )
    thread_id: Optional[str] = Field(
        default=None,
        alias="threadId",
    )
    worker_status: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="workerStatus",
        description="Frontend worker status context",
    )
    mode: ConversationMode = Field(
        default=ConversationMode.NATURAL,
        alias="mode",
        description="Current conversation mode",
    )
    mode_change_origin: Literal["auto", "user"] = Field(
        default="auto",
        alias="modeChangeOrigin",
        description="모드 전환 유래: auto(시스템 자동), user(토글 클릭)",
    )

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

class ChatResponse(BaseModel):
    """Response from the master agent"""
    message: str
    quick_links: List[dict] = Field(default_factory=list, description="List of {label, url} for quick navigation")
    mode: ConversationMode = Field(..., description="Updated conversation mode (Auto-switch result)")
