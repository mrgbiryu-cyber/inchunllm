# -*- coding: utf-8 -*-
"""
StreamContext - v3.2 통합 컨텍스트 객체
모든 stream_message 단계가 공유하는 표준 데이터 구조
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from app.schemas.debug import DebugInfo  # [v4.2]
from app.models.master import ConversationMode # [v4.0]
from app.models.master import ChatMessage

@dataclass
class StreamContext:
    """
    [v3.2] stream_message 전체 흐름에서 공유하는 컨텍스트
    """
    # === 입력 정보 ===
    session_id: str
    project_id: str
    thread_id: Optional[str]
    user_id: Optional[str]
    user_input_raw: str  # 원본 입력
    history: List[ChatMessage] = field(default_factory=list)  # 이전 대화 이력 (최근 라우팅 추적용)
    
    # === [v4.2] 검증/감사 메타데이터 ===
    request_id: str = ""  # 요청 추적 ID (UUID)
    is_admin: bool = False  # Admin 여부
    debug_info: DebugInfo = field(default_factory=DebugInfo)  # 검증 데이터 (Retrieval 결과 등)

    user_input_norm: str = ""  # 정규화된 입력 (공백/줄바꿈 정리)
    
    # === [v4.0] Conversation Mode ===
    mode: ConversationMode = ConversationMode.NATURAL  # 현재 대화 모드
    mode_switched: bool = False  # 모드 전환 발생 여부
    mode_switch_origin: str = "auto"  # auto | user
    
    # === Intent 분류 결과 ===
    llm_intent: Optional[str] = None  # LLM이 추론한 Intent (있으면)
    primary_intent: str = "NATURAL"  # Primary Intent (단일 값)
    flags: List[str] = field(default_factory=list)  # Secondary 신호
    
    # === Shadow Mining 결과 ===
    draft_updates: List[Dict[str, Any]] = field(default_factory=list)  # 새로 생성된 Draft 목록
    
    # === MES 상태 ===
    mes: Dict[str, Any] = field(default_factory=dict)  # 현재 MES (Mission Execution Spec)
    mes_hash: Optional[str] = None  # 현재 MES Hash
    mes_changed: bool = False  # MES가 이번 턴에 변경되었는지
    
    # === Verification 상태 ===
    verification_state: str = "DIRTY"  # VERIFIED / DIRTY
    verified_hash: Optional[str] = None  # VERIFIED 시점의 MES Hash
    verified_at: Optional[datetime] = None  # VERIFIED 시각
    
    # === Tool/DB 조회 결과 (FUNCTION_READ) ===
    tool_facts: Dict[str, Any] = field(default_factory=dict)  # 실시간 Tool 결과만
    tool_error: Optional[str] = None  # Tool 조회 실패 시 에러 메시지
    
    # === confirm_token 감지 ===
    confirm_token: Optional[str] = None  # 명시적 토큰만 (None이면 없음)
    confirm_token_detected: bool = False  # 명시 토큰 감지 여부
    
    # === FUNCTION_WRITE Gate ===
    write_gate_open: bool = False  # Gate Open 여부
    write_gate_reason: Optional[str] = None  # Gate Closed 시 이유
    
    # === 최종 응답 ===
    response_parts: List[str] = field(default_factory=list)  # 응답 조각들
    final_response: str = ""  # 최종 응답 (response_builder가 생성)
    
    # === v1.0 Plan Routing (상담 프로세스 가드) ===
    plan_intent: Optional[str] = None  # PLAN_PROFILE_CAPTURE / PLAN_TEMPLATE_SELECT / PLAN_DRAFT_SECTIONS ...
    plan_confidence: float = 0.0  # plan_intent confidence
    plan_tie_delta: Optional[float] = None  # 상위 2개 점수 차이
    need_disambiguation: bool = False  # 동률/저신뢰 시 사용자 선택 요구
    routing_state: str = "PLAN_FREE_CHAT"  # PLAN_FREE_CHAT / PLAN_QUESTION_FLOW / PLAN_TEMPLATE_SELECT / PLAN_DRAFT_SECTIONS / PLAN_DISAMBIGUATION
    routing_message: Optional[str] = None  # 가드 메시지/안내 텍스트
    routing_options: List[str] = field(default_factory=list)  # 다중 선택 옵션(저신뢰 시)
    routing_error: Optional[Dict[str, Any]] = None  # 실행 불가/카운터 초과 등
    plan_data_version: int = 0
    summary_revision: int = 0
    policy_version: str = "v0_legacy"
    consultation_mode: Optional[str] = None
    active_template_id: Optional[str] = None
    allocated_question_type: Optional[str] = None
    question_counters_snapshot: Optional[Dict[str, Any]] = None
    is_first_login_entry: bool = False
    is_new_room_first_entry: bool = False

    # === 메타데이터 ===
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    step_logs: List[str] = field(default_factory=list)  # 디버깅용 Step 로그
    
    def add_log(self, step_name: str, message: str):
        """Step 로그 추가 (디버깅용)"""
        log_entry = f"[{step_name}] {message}"
        self.step_logs.append(log_entry)
        print(f"DEBUG: {log_entry}", flush=True)
    
    def add_flag(self, flag: str):
        """Flag 추가 (중복 방지)"""
        if flag not in self.flags:
            self.flags.append(flag)
    
    def set_primary_intent(self, intent: str):
        """Primary Intent 설정 (단일 값만)"""
        self.primary_intent = intent
        self.add_log("classify_intent", f"Primary Intent = {intent}")
